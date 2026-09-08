"""agent — 로컬 병렬 멀티에이전트 오케스트레이터 (NEW-METHOD.md 구현체).

myllm 은 "서버 띄우기"만 담당하고, 이 패키지가 오케스트레이터 + RAG 를 짬뽕으로
담는다. 실제 llama.cpp llama-server(OpenAI 호환 /v1/chat/completions)들을
role 매핑(parser/worker/coder/reasoner, 기본 8081~8088)으로 병렬 호출하고,
study_lib 의 RAG(store/retrieve)로 근거 청크만 주입해 환각을 막는다.

설계 원칙 (study_lib 관례를 따름):
- 각 협력자(Registry/Gateway/Planner/RAG/Orchestrator)는 Protocol + 주입으로
  테스트 가능하게 하고, 순수 계약(parsing/계산)은 결정적 fake로 고정한다.
- 실제 네트워크는 gateway(gateway.py)에서만 일어난다 — orchestrator 는
  Gateway/GatewaySession 을 받아 쓴다.
"""
from .registry import Registry, Role, Server
from .gateway import Gateway, GatewayError, OllamaEmbed, strip_thinking
from .planner import PlanParser, Task
from .orchestrator import Orchestrator, WorkerResult, run_plan

__all__ = [
    "Registry", "Role", "Server",
    "Gateway", "GatewayError", "OllamaEmbed", "strip_thinking",
    "PlanParser", "Task",
    "Orchestrator", "WorkerResult", "run_plan",
]
