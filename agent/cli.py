"""cli — 계획서 파일/입력 → 로컬 멀티에이전트 실행 → notes/*.md 저장.

사용:
  python -m agent.cli run plan.txt --notes notes --live
  python -m agent.cli run plan.txt --rag book-store --embed-base ... --notes notes
  python -m agent.cli split plan.txt        # 티켓 분해만 (모델 없이)
  python -m agent.cli roles                 # 현재 등록된 역할/서버 확인

기본은 "로컬/가벼운 모드"(--live 없으면 RAG 를 KeywordRetriever 로 대체해도
동작; 실제 llama-servers 를 쓸 때만 --live). 서버 배치는 서버 계정에서 포트를
env/agent.yaml 로 잡고 live 로 돈다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---- 선택적 의존성 게이트: RAG 를 실제 study store 로 열 때만 import 시킨다 --------


def _make_rag(args, registry):
    """--rag 지정에 따라 retriever 구성 (실제 store / keyword 폴백)."""
    # 1) 실제 study_lib store (서버 배치 권장; 로컬 데모는 store 없으면 폴백)
    if args.rag == "book-store":
        from agent.rag import StudyStoreRetriever
        try:
            return StudyStoreRetriever.load(
                store_dir=args.store,
                embed_base=(registry.role("embed").base_url),
                embed_model=args.embed_model,
            )
        except RuntimeError as exc:
            if not args.live:
                print(f"[rag] book-store unavailable, keyword fallback → {exc}")
                return None
            raise
    # 2) 코드 인덱스 (§4.2) — 여기선 뼈대만. 실제 AST 인덱서가 붙으면 대체.
    if args.rag == "code":
        from agent.rag import CodeIndex
        ci = CodeIndex()
        if args.code_file:
            from agent.rag import CodeSymbol
            # 초간단: 파일을 한 심볼로만 넣는 데모 지점 — 실제는 tree-sitter 로 분해.
            text = Path(args.code_file).read_text(encoding="utf-8")
            ci.index_symbol(CodeSymbol(path=str(args.code_file), name="<file>",
                                       kind="module", doc=text[:2000]))
        return ci
    return None  # 근거 없음 → worker 가 "source 없음" 안내


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agent", description=__doc__)
    sub = ap.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="계획서 실행")
    p_run.add_argument("plan", nargs="?",
                       help="계획 파일 경로 (없으면 stdin)")
    p_run.add_argument("--notes", default="notes",
                       help="결과 저장 dir (기본 ./notes)")
    p_run.add_argument("--rac", "-r", dest="rag", default=None,
                       choices=["book-store", "code"],
                       help="RAG 엔진 (기본 None → 근거 없음)")
    p_run.add_argument("--store", default=None,
                       help="study store jsonl dir (book-store 일 때)")
    p_run.add_argument("--embed-model", default="qwen2.5:3b")
    p_run.add_argument("--code-file", default=None)
    p_run.add_argument("--live", action="store_true",
                       help="실제 llama-servers(8081-8088) 호출. 없으면 모의로 worker 출력")
    p_run.add_argument("--parser-live", action="store_true",
                       help="parser 서버(8081)로 티켓 정규화. 없으면 로컬 split_plan")
    p_run.add_argument("--stem", default=None, help="결과 파일 stem")

    p_split = sub.add_parser("split", help="티켓 분해만 출력 (모델 없음)")
    p_split.add_argument("plan", nargs="?",
                         help="계획 파일 경로 (없으면 stdin)")
    sub.add_parser("roles", help="현재 역할/서버 확인")

    args = ap.parse_args(argv)
    from agent.registry import Registry, load_registry

    if not args.cmd:
        ap.print_help()
        return 2

    if args.cmd == "roles":
        reg = load_registry()
        for name, role in reg.roles().items():
            print(f"{name:10s} kind={role.kind:6s} urls={','.join(role.urls)}")
        return 0

    text = _read_plan(args)
    if args.cmd == "split":
        from agent.planner import split_plan
        print(json.dumps([t.__dict__ for t in split_plan(text)],
                         ensure_ascii=False, default=str))
        return 0

    # run
    from agent.gateway import Gateway, GatewayError
    from agent.orchestrator import Orchestrator
    from agent.planner import PlanParser, split_plan

    reg = load_registry()

    # gateway factory — --live 가 아니면 전부 "모의 worker" (테스트/오프라인 데모)
    live = args.live

    def factory(url: str, role: str):
        if live:
            import os
            os.environ.setdefault("AGENT_NOTES_DIR", args.notes)
            return Gateway(base_url=url)
        return _MockGateway(url, role)

    parser = None
    if args.parser_live:
        parser = PlanParser(Gateway(reg.role("parser").base_url))

    rag = _make_rag(args, reg) if args.rag else None
    # live 이면 다른(논리적) 모델을 판사로 구성해 답 검증 (offline 은 판사 off)
    vf = None
    if live:
        try:
            from agent.verify import LlmVerifier, pick_judge_server
            def vf(role):
                try:
                    u = pick_judge_server(role, reg)
                except ValueError:
                    return None
                return LlmVerifier(Gateway(base_url=u), judge_role=u.rpartition(":")[2])
        except Exception:  # noqa: BLE001
            vf = None
    orch = Orchestrator(registry=reg, rag=rag,
                        gateway_factory=factory, parser=parser,
                        note_dir=args.notes, verifier_factory=vf)
    if live and args.parser_live is False:
        # parser 서버가 안 켜져 있으면 오케스트레이터가 split_plan 으로 처리하도록
        print("[cli] live without parser-live → tickets via local split_plan")
    results, path = orch.run_plan(text, note_stem=args.stem)
    _print_summary(results)
    if path:
        print(f"\n📄 결과 저장: {path}")
    return 0


class _MockGateway:
    """--live 없을 때 오프라인/데모: worker·coder·reasoner 를 서버 안 부르고
    결정적 텍스트로 응답 (연결·CI 실패 없이 파이프라인 동작 검증용)."""

    def __init__(self, url: str, role: str) -> None:
        self.url = url
        self.role = role

    def chat(self, *, system: str, user: str, max_tokens: int = 2000,
             temperature: float = 0.0, model: str | None = None) -> str:
        # user 의 REFERENCE CONTEXT 를 그대로 돌려줘 환각 없음을 보이는 것이 아니라,
        # 실제 모델을 대신해 "요청 내용 재구성" 반환.
        q = user.split("QUESTION / TICKET INPUT:")[-1].split("REFERENCE CONTEXT")[0].strip()
        return f"({self.role} echo @ {self.url})\n{q[:400] or '(no input)'}"

    def chat_json(self, **kw):
        raise GatewayError("_MockGateway has no JSON parser (parser-live only)")


def _read_plan(args) -> str:
    if getattr(args, "plan", None):
        return Path(args.plan).read_text(encoding="utf-8")
    return sys.stdin.read()


def _print_summary(results) -> None:
    ok = sum(1 for r in results if r.ok)
    print(f"✔ workers ok {ok}/{len(results)}")
    for r in results:
        if r.error:
            print(f"  ✗ Task{r.task} · {r.role}: {r.error[:120]}")
        else:
            print(f"  ✓ Task{r.task} · {r.role}  [{len(r.output)} chars] {r.url}")


if __name__ == "__main__":
    raise SystemExit(main())
