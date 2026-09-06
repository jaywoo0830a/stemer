"""과목 식별자 — 코어 외에 주제 팩(확장 키)이 존재하는 과목들."""
SUBJECTS = ("math", "phys", "chem", "bio")


def require_subject(subject: str) -> str:
    if subject not in SUBJECTS:
        raise ValueError(f"unknown subject {subject!r}; expected {SUBJECTS}")
    return subject
