"""Logging utilities for the pub-sub system."""
import sys
from datetime import datetime
from typing import Optional


class Colors:
    """ANSI color codes for terminal output."""
    RESET = '\033[0m'
    BOLD = '\033[1m'

    # Regular colors
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'

    # Bright colors
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'


def _format_timestamp() -> str:
    """Format current timestamp for logging."""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log_info(component: str, message: str) -> None:
    """Log an informational message."""
    timestamp = _format_timestamp()
    print(f"{Colors.BRIGHT_BLACK}[{timestamp}]{Colors.RESET} "
          f"{Colors.CYAN}ℹ {Colors.BOLD}{component}{Colors.RESET} "
          f"{Colors.CYAN}│{Colors.RESET} {message}")


def log_success(component: str, message: str) -> None:
    """Log a success message."""
    timestamp = _format_timestamp()
    print(f"{Colors.BRIGHT_BLACK}[{timestamp}]{Colors.RESET} "
          f"{Colors.GREEN}✓ {Colors.BOLD}{component}{Colors.RESET} "
          f"{Colors.GREEN}│{Colors.RESET} {message}")


def log_warning(component: str, message: str) -> None:
    """Log a warning message."""
    timestamp = _format_timestamp()
    print(f"{Colors.BRIGHT_BLACK}[{timestamp}]{Colors.RESET} "
          f"{Colors.YELLOW}⚠ {Colors.BOLD}{component}{Colors.RESET} "
          f"{Colors.YELLOW}│{Colors.RESET} {message}")


def log_error(component: str, message: str) -> None:
    """Log an error message."""
    timestamp = _format_timestamp()
    print(f"{Colors.BRIGHT_BLACK}[{timestamp}]{Colors.RESET} "
          f"{Colors.RED}✗ {Colors.BOLD}{component}{Colors.RESET} "
          f"{Colors.RED}│{Colors.RESET} {message}", file=sys.stderr)


def log_debug(component: str, message: str) -> None:
    """Log a debug message."""
    timestamp = _format_timestamp()
    print(f"{Colors.BRIGHT_BLACK}[{timestamp}]{Colors.RESET} "
          f"{Colors.BRIGHT_BLACK}◆ {Colors.BOLD}{component}{Colors.RESET} "
          f"{Colors.BRIGHT_BLACK}│{Colors.RESET} {Colors.BRIGHT_BLACK}{message}{Colors.RESET}")


def log_network(component: str, action: str, details: str) -> None:
    """Log a network-related message."""
    timestamp = _format_timestamp()
    print(f"{Colors.BRIGHT_BLACK}[{timestamp}]{Colors.RESET} "
          f"{Colors.MAGENTA}⇄ {Colors.BOLD}{component}{Colors.RESET} "
          f"{Colors.MAGENTA}│{Colors.RESET} {Colors.BRIGHT_MAGENTA}{action}{Colors.RESET} {details}")


def log_system(component: str, message: str) -> None:
    """Log a system-level message."""
    timestamp = _format_timestamp()
    print(f"{Colors.BRIGHT_BLACK}[{timestamp}]{Colors.RESET} "
          f"{Colors.BRIGHT_BLUE}⚙ {Colors.BOLD}{component}{Colors.RESET} "
          f"{Colors.BRIGHT_BLUE}│{Colors.RESET} {message}")


def log_separator(title: Optional[str] = None) -> None:
    """Print a visual separator."""
    if title:
        print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}{'─' * 20} {title} {'─' * 20}{Colors.RESET}")
    else:
        print(f"{Colors.BRIGHT_BLACK}{'─' * 60}{Colors.RESET}")


def log_header(title: str) -> None:
    """Print a header for major sections."""
    print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}╔{'═' * 58}╗{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}║{Colors.RESET} "
          f"{Colors.BOLD}{Colors.BRIGHT_WHITE}{title.center(56)}{Colors.RESET} "
          f"{Colors.BOLD}{Colors.BRIGHT_CYAN}║{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}╚{'═' * 58}╝{Colors.RESET}\n")
