"""Shared logging configuration for entry points.

Every entry point (movies ``main.py``, dashboard ``app.py`` / ``orchestrate.py``)
called ``logging.basicConfig(...)`` with a near-identical format and then quieted
``httpx`` (and a few others) so per-request INFO logs don't drown real output.
:func:`configure_logging` centralises that.

Secrets never reach the log, and that is enforced at the **output boundary** rather
than per call site: pass ``secrets=`` and :func:`configure_logging` installs a
formatter that scrubs them out of every rendered record. See :class:`RedactingFormatter`
for why that layer and not another.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Libraries that log one INFO line per network call — quieted to WARNING by default.
_DEFAULT_QUIET = ("httpx",)


def redact(value: object, *secrets: str | None) -> str:
    """Stringify ``value`` with every non-empty secret replaced by ``***``.

    The primitive behind :class:`RedactingFormatter`; rarely needed directly, since
    the formatter already covers anything that goes through logging.

    Substring replacement rather than URL parsing on purpose: it holds whatever the
    text turns out to be — a message, a rendered traceback, the ``repr`` of a request
    object — instead of only the one field a parser knows to look at. Empty and
    ``None`` secrets are skipped, so an unset key cannot turn ``str.replace`` into a
    separator between every character.
    """
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


class RedactingFormatter(logging.Formatter):
    """A formatter that scrubs known secrets out of everything it renders.

    TMDB authenticates by query parameter, so the live key sits inside the request
    URL — and httpx embeds that URL in the string form of ``HTTPStatusError``. A bare
    ``logger.warning("%s", exc)`` therefore writes the key to the log, which is
    exactly what shipped (DEBUG in ``movies_management``, WARNING in the dashboard's
    streaming fetch, i.e. at the default level).

    **Why the formatter and not the obvious alternatives**, all three measured:

    * *Calling ``redact()`` at each log site* works, but is a rule to remember at
      every future one, and covers none of the loggers we don't own.
    * *A custom exception overriding ``__str__``/``__repr__``* protects its own
      message but **leaks through the ``__cause__`` chain**: ``raise Wrapper from exc``
      then ``logger.exception(...)`` renders the original traceback, key and all.
      Avoiding that needs ``from None``, which throws away the debugging context.
    * *This formatter* runs on the fully rendered string, so it covers the message,
      the traceback (chained causes included), and third-party loggers like ``httpx``
      and ``tenacity`` — with nothing required of any call site.

    The residual gap: it only covers logging. An exception escaping to
    ``sys.excepthook`` is printed by the interpreter, not by a handler. Nothing
    propagates that far here — the TMDB helpers all catch and return ``None`` — but
    the real fix at the source would be sending the credential as a header instead of
    a query parameter, which TMDB only supports with a different (v4) credential.
    """

    def __init__(self, fmt: str | None = None, datefmt: str | None = None, *, secrets: Iterable[str | None] = ()) -> None:
        # Narrower than ``Formatter.__init__`` (no ``style``/``validate``/``defaults``)
        # on purpose: this class has two call sites and both pass fmt + datefmt. A
        # ``*args: object`` passthrough only bought a ``type: ignore`` and five ty
        # diagnostics.
        super().__init__(fmt, datefmt)
        self._secrets = tuple(s for s in secrets if s)

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), *self._secrets)


def configure_logging(
    level: str | int = "INFO",
    *,
    fmt: str = _DEFAULT_FORMAT,
    datefmt: str = _DEFAULT_DATEFMT,
    quiet: Iterable[str] = _DEFAULT_QUIET,
    secrets: Iterable[str | None] = (),
) -> logging.Logger:
    """Configure root logging and quiet noisy network loggers.

    ``level`` accepts a name (``"INFO"``, case-insensitive) or a numeric level.
    ``secrets`` are API keys to scrub from every record — pass the entry point's keys
    (``settings.tmdb_api_key``, …) and no call site needs to think about it again;
    ``None``/empty entries are ignored, so an unset key is harmless.
    Returns the root logger for convenience.
    """
    resolved = level.upper() if isinstance(level, str) else level
    logging.basicConfig(level=resolved, format=fmt, datefmt=datefmt)
    # basicConfig only sets the level when the root has no handlers yet; set it
    # explicitly so the requested level also takes effect when handlers already
    # exist (e.g. under pytest's log capture, or a second call).
    root = logging.getLogger()
    root.setLevel(resolved)
    # Same reason the level is set explicitly: basicConfig will not have touched the
    # formatter when handlers already exist, and the scrubbing has to reach every one
    # of them to be a guarantee rather than a default.
    formatter = RedactingFormatter(fmt, datefmt, secrets=secrets)
    for handler in root.handlers:
        handler.setFormatter(formatter)
    for name in quiet:
        logging.getLogger(name).setLevel(logging.WARNING)
    return root
