"""
High level, asynchronous framework for writing mail filters

Kilter is a framework for writing mail filters (known as "milters")
compatible with Sendmail and Postfix MTAs.  Unlike many previous milter implementations in
Python it is not simply bindings to the libmilter library (originally from the Sendmail
project).  The framework aims to provide Pythonic interfaces for implementing filters,
including leveraging coroutines instead of libmilter's callback-style interface.
"""

from ..protocol import ResponseMessage
from .runner import Runner
from .session import END
from .session import START
from .session import After
from .session import Before
from .session import Session

__version__ = "0.2.0"

__all__ = [
	"After",
	"Before",
	"END",
	"ResponseMessage",
	"Runner",
	"START",
	"Session",
]
