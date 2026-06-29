"""
Entry-point — python main.py
"""
import logging
import structlog
import uvicorn

from aws_devops_agent.config import get_settings

# ── Structured logging setup ─────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)


def main():
    cfg = get_settings()
    uvicorn.run(
        "aws_devops_agent.server:app",
        host=cfg.api_host,
        port=cfg.api_port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
