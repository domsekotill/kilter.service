[![gitlab-ico]][gitlab-link]
[![licence-mpl20]](/LICENCE.txt)
[![pre-commit-ico]][pre-commit-link]
[![pipeline-status]][pipeline-report]
[![coverage status]][coverage report]


Kilter Service
==============

Kilter is a framework for writing [mail filters](#sendmail-filters) (known as "milters") 
compatible with Sendmail and Postfix MTAs.  Unlike many previous milter implementations in 
Python it is not simply bindings to the `libmilter` library (originally from the Sendmail 
project).  The framework aims to provide Pythonic interfaces for implementing filters, 
including leveraging coroutines instead of `libmilter`'s callback-style interface.

The `kilter.service` package contains the higher-level, asynchronous framework for writing 
mail filters.


Sendmail Filters
----------------

The Sendmail filter (milter) API facilitates communication between a Mail Transfer Agent 
(MTA) and arbitrary filters running as external services.  These filters can perform 
a number of operations on received and outgoing mail, such as: virus scanning; checking 
senders' reputations; signing outgoing mail; and verifying signatures of incoming mail.

While the protocol was originally for filtering mail through a Sendmail MTA, Postfix has 
also reverse engineered the protocol and supports most filters made for Sendmail.


`libmilter`
-----------

Historically filters used the `libmilter` library supplied by the Sendmail project to handle 
all aspects of communication with an MTA.  Filters simply registered callbacks for various 
events then started the library's main loop. This approach makes implementing simple filters 
in C easy for users, but makes writing "Pythonic" filters difficult, especially when a user 
wishes to make use of async/await features.

Use of `libmilter` to implement filters is almost universal as it is a black-box; the 
on-the-wire protocol used is undocumented and subject to change between versions, which 
makes writing a third-party parser difficult.


Usage
=====

To write filters, create a coroutine function that conforms to `Filter`.  This function 
takes a `Session` object as its only argument.

`Session` instances have several awaitable methods corresponding to SMTP commands 
(i.e. `MAIL`, `RCPT`) and instances of `HeadersAccessor` and `BodyAccessor` which have 
awaitable methods and are themselves asynchronous iterators.  The various methods await 
particular messages from an MTA and may return appropriate values from them.  The 
asynchronous iterators yield repeating messages like `kilter.protocol.Header` and 
`kilter.protocol.Body`.


Examples
--------

The following is a contrived example showing a filter that rejects messages sent by 
a particular user:

```python
from kilter.service import Session
from kilter.protocol import Reject, Accept

# This corncob doesn't know when to stop; block him
BLOCK = b"the.black.knight@example.com"

async def reject_black_knight(session: Session) -> Reject|Accept:
	if (await session.envelope_from()) == BLOCK:
		return Reject()

	async with session.headers as headers:
		async for header in headers:
			if header.name == "From" and header.value == BLOCK:
				return Reject()

	return Accept()
```

The following two examples show two implementations of a filter that strips headers starting 
with "X-".  They demonstrate the two methods of collecting headers, then later modifying 
them during the post phase.

```python
from kilter.service import Session
from kilter.protocol import Accept

async def strip_x_headers(session: Session) -> Accept:
	remove = []

	# iterate over headers as they arrive and select ones for later removal
	async with session.headers as headers:
		async for header in headers:
			if header.name.startswith("X-"):
				remove.append(header)

	# remove the selected headers during the post phase
	for header in remove:
		await session.headers.remove(header)
```

```python
from kilter.service import Session
from kilter.protocol import Accept

async def strip_x_headers(session: Session) -> Accept:
	# collect the headers first
	await session.headers.collect()

	# iterate over collected headers during the post phase, removing the unwanted ones
	async with session.headers as headers:
		async for header in headers:
			if header.name.startswith("X-"):
				await session.headers.remove(header)
```


---

[gitlab-ico]:
  https://img.shields.io/badge/GitLab-code.kodo.org.uk-blue.svg?logo=gitlab
  "GitLab"

[gitlab-link]:
  https://code.kodo.org.uk/kilter/kilter.service
  "Kilter Service at code.kodo.org.uk"

[pre-commit-ico]:
  https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white
  "Pre-Commit: enabled"

[pre-commit-link]:
  https://github.com/pre-commit/pre-commit
  "Pre-Commit at GitHub.com"

[licence-mpl20]:
  https://img.shields.io/badge/Licence-MPL--2.0-blue.svg
  "Licence: Mozilla Public License 2.0"

[pipeline-status]:
  https://code.kodo.org.uk/kilter/kilter.service/badges/main/pipeline.svg

[pipeline-report]:
  https://code.kodo.org.uk/kilter/kilter.service/pipelines/latest
  "Pipelines"

[coverage status]:
  https://code.kodo.org.uk/kilter/kilter.service/badges/main/coverage.svg

[coverage report]:
  https://code.kodo.org.uk/kilter/kilter.service/-/jobs/artifacts/main/file/results/coverage/index.html?job=Unit+Tests
