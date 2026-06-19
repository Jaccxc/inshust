from inshust.app import run as _run


def main() -> None:
    """Console entry point.

    Delegates to :func:`inshust.app.run`. This slice loads config and reports
    the wired walking skeleton; auth + realtime/poll listener land in later
    cards (see the PRD and README).
    """
    _run()
