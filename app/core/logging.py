import logging
from rich.logging import RichHandler
from rich.traceback import install

install(show_locals=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)]
)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
