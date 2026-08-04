import time


class log_elapsed:
    """Context manager that logs wall-clock elapsed time for one pipeline
    stage. Logs a single greppable "TIMING [label] N.Nms" line via the given
    logger on exit (even on exception, so a failing stage still reports how
    long it ran before failing) — used to build a real, multi-level latency
    breakdown of a live run rather than guessing which stage is slow."""

    def __init__(self, logger, label):
        self.logger = logger
        self.label = label

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        elapsed_ms = (time.perf_counter() - self.t0) * 1000
        self.logger.info(f"TIMING [{self.label}] {elapsed_ms:.1f}ms")
        return False
