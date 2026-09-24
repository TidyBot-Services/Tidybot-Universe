"""Compatibility CLI for the independently packaged Memory Service API."""

from attention_memory_service.memory_service_api import (
    CandidateInput,
    PairInput,
    RetrieveInput,
    create_app,
    main,
)

__all__ = ["CandidateInput", "PairInput", "RetrieveInput", "create_app", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
