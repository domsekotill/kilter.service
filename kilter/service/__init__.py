"""
High level, asynchronous framework for writing mail filters

Kilter is a framework for writing mail filters (known as "milters")
compatible with Sendmail and Postfix MTAs.  Unlike many previous milter implementations in
Python it is not simply bindings to the libmilter library (originally from the Sendmail
project).  The framework aims to provide Pythonic interfaces for implementing filters,
including leveraging coroutines instead of libmilter's callback-style interface.
"""

from .session import ResponseMessage as ResponseMessage
from .session import Session as Session
from .session import Before as Before
from .session import After as After
from .session import START as START
from .session import END as END

__version__ = "0.1"
