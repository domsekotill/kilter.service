# Copyright 2023 Dominik Sekotill <dom.sekotill@kodo.org.uk>
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Filter decorators for marking the requested protocol options and actions used
"""

from __future__ import annotations

from typing import Callable
from typing import Literal
from typing import NamedTuple

from kilter.protocol.messages import ActionFlags
from kilter.protocol.messages import ProtocolFlags

from .session import Filter

__all__ = [
	"responds_to_connect", "examine_helo",
	"examine_sender", "examine_recipients",
	"examine_headers", "examine_body",
	"get_flags", "modify_flags",
]

Decorator = Callable[[Filter], Filter]
SIZES = Literal[ProtocolFlags.NONE, ProtocolFlags.MDS_256K, ProtocolFlags.MDS_1M]

FLAGS_ATTRIBUTE = "filter_flags"

DEFAULT_UNSET = \
	ProtocolFlags.NO_CONNECT | ProtocolFlags.NO_HELO | \
	ProtocolFlags.NO_SENDER | ProtocolFlags.NO_RECIPIENT | \
	ProtocolFlags.NO_DATA | ProtocolFlags.NO_BODY | \
	ProtocolFlags.NO_HEADERS | ProtocolFlags.NO_END_OF_HEADERS | \
	ProtocolFlags.NO_UNKNOWN | \
	ProtocolFlags.NR_CONNECT | ProtocolFlags.NR_HELO | \
	ProtocolFlags.NR_SENDER | ProtocolFlags.NR_RECIPIENT | \
	ProtocolFlags.NR_DATA | ProtocolFlags.NR_BODY | \
	ProtocolFlags.NR_HEADER | ProtocolFlags.NR_END_OF_HEADERS | \
	ProtocolFlags.NR_UNKNOWN


class FlagsTuple(NamedTuple):

	unset_options: ProtocolFlags = ProtocolFlags.NONE
	set_options: ProtocolFlags = ProtocolFlags.NONE
	set_actions: ActionFlags = ActionFlags.NONE


def modify_flags(
	set_options: ProtocolFlags = ProtocolFlags.NONE,
	unset_options: ProtocolFlags = ProtocolFlags.NONE,
	set_actions: ActionFlags = ActionFlags.NONE,
) -> Decorator:
	"""
	Return a decorator that modifies the given flags on a decorated filter
	"""
	def decorator(filtr: Filter) -> Filter:
		flags = _get_flags(filtr, FlagsTuple())
		flags = FlagsTuple(
			flags.unset_options|unset_options,
			flags.set_options|set_options,
			flags.set_actions|set_actions,
		)
		setattr(filtr, FLAGS_ATTRIBUTE, flags)
		return filtr
	return decorator


def get_flags(filtr: Filter) -> FlagsTuple:
	"""
	Return the flags attached to a filter
	"""
	default = FlagsTuple(unset_options=DEFAULT_UNSET, set_actions=ActionFlags.ALL)
	return _get_flags(filtr, default)


def _get_flags(filtr: Filter, default: FlagsTuple) -> FlagsTuple:
	assert isinstance(getattr(filtr, FLAGS_ATTRIBUTE, default), FlagsTuple)
	return getattr(filtr, FLAGS_ATTRIBUTE, default)


def responds_to_connect() -> Decorator:
	"""
	Mark a filter as possibly delivering a non-continue response to Connect events
	"""
	return modify_flags(unset_options=ProtocolFlags.NR_CONNECT)


def examine_helo(
	can_respond: bool = False,
) -> Decorator:
	"""
	Mark a filter as needing to examine the HELO command

	If `can_respond` is `False` the filter runner will attempt to negotiate faster event
	delivery by disabling the need to respond to this event.
	"""
	unset = ProtocolFlags.NO_HELO
	if can_respond:
		unset |= ProtocolFlags.NR_HELO
	return modify_flags(unset_options=unset)


def examine_sender(
	can_respond: bool = False,
	can_replace: bool = False,
) -> Decorator:
	"""
	Mark a filter as needing to examine and optionally replace the RCPT FROM sender

	If `can_respond` is `False` the filter runner will attempt to negotiate faster event
	delivery by disabling the need to respond to this event.

	If `can_replace` is `True` but is not offered by the MTA an exception will be raised
	during negotiation and the filter will be disabled.
	"""
	unset = ProtocolFlags.NO_SENDER
	if can_respond:
		unset |= ProtocolFlags.NR_SENDER
	return modify_flags(
		unset_options=unset,
		set_actions=ActionFlags.CHANGE_FROM if can_replace else ActionFlags.NONE,
	)


def examine_recipients(
	can_respond: bool = False,
	can_add: bool = False,
	can_remove: bool = False,
	include_rejected: bool= False,
	with_parameters: bool = False,
) -> Decorator:
	"""
	Mark a filter as needing to examine and optionally modify the RCPT TO recipients

	If `can_respond` is `False` the filter runner will attempt to negotiate faster event
	delivery by disabling the need to respond to this event.

	If `include_rejected` is `True` the recipients available to the filter will include any
	that the MTA or another filter has already rejected.

	The option `with_parameters` enables the use of RFC-1425 [section 6] extensions for
	"MAIL" commands (ratified by RFC-5321) when adding recipients.  The specific details of
	any extension parameters will be dependent on the MTA.

	If a requested option or update action is not offered by the MTA an exception will be
	raised during negotiation and the filter will be disabled.
	"""
	unset = ProtocolFlags.NO_RECIPIENT
	opts = ProtocolFlags.NONE
	acts = ActionFlags.NONE
	if can_respond:
		unset |= ProtocolFlags.NR_RECIPIENT
	if can_add:
		acts |= ActionFlags.ADD_RECIPIENT
	if can_add and with_parameters:
		acts |= ActionFlags.ADD_RECIPIENT_PAR
	if can_remove:
		acts |= ActionFlags.DELETE_RECIPIENT
	if include_rejected:
		opts |= ProtocolFlags.REJECTED_RECIPIENT
	return modify_flags(unset_options=unset, set_options=opts, set_actions=acts)


def examine_headers(
	can_respond: bool = False,
	can_add: bool = False,
	can_modify: bool = False,
	leading_space: bool = False,
) -> Decorator:
	"""
	Mark a filter as needing to examine and optionally add or modify message headers

	If `can_respond` is `False` the filter runner will attempt to negotiate faster event
	delivery by disabling the need to respond to this event.

	If `leading_space` is `True` the headers will be delivered without any whitespace
	removed from values (i.e. after the separating colon). This is for filters which need
	the exact bytes contained in message headers.

	If a requested option or update action is not offered by the MTA an exception will be
	raised during negotiation and the filter will be disabled.
	"""
	unset = ProtocolFlags.NO_HEADERS
	opts = ProtocolFlags.NONE
	acts = ActionFlags.NONE
	if can_respond:
		unset |= ProtocolFlags.NR_HEADER
	if can_add:
		acts |= ActionFlags.ADD_HEADERS
	if can_modify:
		acts |= ActionFlags.CHANGE_HEADERS
	if leading_space:
		opts |= ProtocolFlags.HEADER_LEADING_SPACE
	return modify_flags(unset_options=unset, set_options=opts, set_actions=acts)


def examine_body(
	can_respond: bool = False,
	can_replace: bool = False,
	data_size: SIZES = ProtocolFlags.NONE,
) -> Decorator:
	"""
	Mark a filter as needing to examine and optionally replace message bodies

	If `can_respond` is `False` the filter runner will attempt to negotiate faster event
	delivery by disabling the need to respond to this event.

	The `data_size` option is a hint, and does not guarantee that the message will be
	delivered in blocks of that size. If `ProtocolFlags.NONE` (the default) the MTA's
	default will be used.

	If `can_replace` is `True` but is not offered by the MTA an exception will be raised
	during negotiation and the filter will be disabled.
	"""
	unset = ProtocolFlags.NO_BODY
	if can_respond:
		unset |= ProtocolFlags.NR_BODY
	return modify_flags(
		unset_options=unset, set_options=data_size,
		set_actions=ActionFlags.CHANGE_BODY if can_replace else ActionFlags.NONE,
	)
