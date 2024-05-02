"""
A Sphinx extension to make cross-linking work without polluting docstrings with RST markup

Docstring text surrounded by single backticks with optional calling parentheses is examined
to see if it can be resolved into a Python object, which is then injected as
a ":py:xxx:`<object>`" style link.
"""

from __future__ import annotations

import builtins
import re
import sys
from collections.abc import Callable
from importlib import import_module
from inspect import get_annotations
from types import FunctionType
from types import MethodType
from types import ModuleType
from typing import Literal as L
from typing import Union

from sphinx.application import Sphinx
from sphinx.util import logging

ObjType = Union[
	L["module"], L["class"], L["exception"], L["function"],
	L["method"], L["attribute"], L["property"],
]


class UnknownObject(ValueError):
	"""
	An error for unknown values of "what" or unusable "obj" passed to `add_roles`
	"""


def setup(app: Sphinx) -> dict[str, object]:
	app.connect("autodoc-process-docstring", add_roles)
	app.connect("autodoc-process-docstring", mark_admonitions)
	return dict(
		parallel_read_safe=True,
	)


def add_roles(
	app: Sphinx, what: ObjType, name: str, obj: object, options: object, lines: list[str],
) -> None:
	"""
	Add Sphinx roles to strings delimited with "`" in docstrings

	Polluting docstrings with RestructuredText markup is forbidden, so this plugin marks-up
	python objects in backticks for cross linking.
	"""
	replacer = get_replacer(what, obj, name)
	regex = re.compile(r"(?<![:])`(?P<name>(?P<identifier>[a-z0-9_.]+)(\(\))?)`", re.I)
	lines[:] = (regex.sub(replacer, line) for line in lines)


def get_replacer(
	what: ObjType, doc_obj: object, doc_obj_name: str,
) -> Callable[[re.Match[str]], str]:
	try:
		module, cls = get_context(what, doc_obj, doc_obj_name)
	except UnknownObject:
		return lambda m: m.group(0)

	def get_type(match: re.Match[str]) -> str:
		"""
		Given a match for a dot-identifier, return the RST type
		"""
		identifier = match.group("identifier")
		name = match.group("name")
		try:
			obj, parent, identifier = dot_import(module, cls, identifier)
		except AttributeError:
			location = None
			if isinstance(doc_obj, FunctionType):
				co = doc_obj.__code__
				location = (co.co_filename, co.co_firstlineno)
			logging.getLogger(__name__).warning(
				f"ignoring {match.group(0)} in docstring of {doc_obj_name}",
				type="ref.ref",
				location=location,
			)
			return match.group(0)
		if isinstance(obj, ModuleType):
			role = ":py:mod:"
		elif isinstance(obj, type):
			role = ":py:exc:" if issubclass(obj, BaseException) else ":py:class:"
		elif callable(obj):
			role = ":py:meth:" if isinstance(parent, type) else ":py:func:"
		elif isinstance(parent, ModuleType):
			role = ":py:const:" if identifier.isupper() else ":py:data:"
		elif isinstance(parent, type):
			role = ":py:attr:"
		else:
			role = ":py:obj:"
		if isinstance(parent, ModuleType):
			ref = f"{name.removeprefix(parent.__name__+'.')} <{identifier}>"
		elif isinstance(parent, type):
			ref = f"{name.removeprefix(parent.__module__+'.')} <{identifier}>"
		else:
			ref = name
		return f"{role}`{ref}`"

	return get_type


def get_context(what: ObjType, obj: object, name: str) -> tuple[ModuleType, type|None]:
	"""
	Given an object and its type, return the module it's in and a class if appropriate

	These values form the starting points for searching for names.
	"""
	match what:
		case "module":
			assert isinstance(obj, ModuleType)
			return obj, None
		case "method":
			assert isinstance(obj, FunctionType|MethodType)
			return get_method_context(obj)
		case "property":
			assert isinstance(obj, property)
			func = \
				obj.fget if isinstance(obj.fget, FunctionType) else \
				obj.fset if isinstance(obj.fset, FunctionType) else \
				None
			if func is None:
				raise UnknownObject(
					"could not get function from property; cannot determine a module",
				)
			return get_method_context(func)
		case "class" | "exception":
			assert isinstance(obj, type), f"{what} {obj!r} is not a type?!"
			return import_module(obj.__module__), obj
		case "function":
			assert hasattr(obj, "__module__"), f"{what} {obj!r} has no attribute '__module__'"
			return import_module(obj.__module__), None
		case "attribute" | "data":
			# Only thing to go on is the name, which *should* be fully qualified
			_, parent, _ = dot_import(sys.modules["builtins"], None, name)
			if isinstance(parent, type):
				return sys.modules[parent.__module__], parent
			if isinstance(parent, ModuleType):
				return parent, None
	logging.getLogger(__name__).warning(f"unknown object type: :{what}:")
	return get_context("attribute", obj, name)


def get_method_context(method: FunctionType) -> tuple[ModuleType, type|None]:
	"""
	Given a method, return it's module and best attempt at a class
	"""
	mod = import_module(method.__module__)
	clsname, has_clsname, _ = method.__qualname__.rpartition(".")
	if not has_clsname:
		return mod, None
	return mod, getattr(mod, clsname, None)


def dot_import(module: ModuleType, cls: type|None, name: str) -> tuple[object, object, str]:
	"""
	Given a dot-separated name, return an object, its parent, and an absolute name for it

	The search is started from the context returned by `get_context()`.
	"""
	labels = list(name.split("."))
	obj, parent, name = dot_import_first(module, cls, labels.pop(0))
	for label in labels:
		parent = obj
		match obj:
			case ModuleType():
				obj = dot_import_from(obj, label)
			case type():
				try:
					obj = getattr(obj, label)
				except AttributeError:
					assert isinstance(obj, type)  # come on mypy…
					annotations = get_annotations(obj)
					if label not in annotations:
						raise
					obj = annotations[label]
			case _:
				obj = getattr(obj, label)
	return obj, parent, ".".join([name] + labels)


def dot_import_first(module: ModuleType, cls: type|None, name: str) -> tuple[object, object, str]:
	"""
	Given a name, return an object, its parent, and its absolute dot-separated name

	The name is search first from builtins; then top-level packages and modules; then
	submodules of the context module; then attributes of the context modules; then
	attributes of the context class, or as a special case the context class itself if the
	name is "self".
	"""
	try:
		return getattr(builtins, name), None, name
	except AttributeError:
		pass
	try:
		return import_module(name), None, name
	except ModuleNotFoundError:
		pass
	try:
		obj = dot_import_from(module, name)
		if hasattr(obj, "__module__"):
			module = import_module(obj.__module__)
		return obj, module, f"{module.__name__}.{name}"
	except AttributeError:
		if cls is None:
			raise
		return (
			(cls, module, f"{module.__name__}.{cls.__name__}") if name == "self" else
			(getattr(cls, name), cls, f"{module.__name__}.{cls.__name__}.{name}")
		)


def dot_import_from(module: ModuleType, name: str) -> object:
	"""
	Given a module and name, return a submodule or module attribute of that name
	"""
	try:
		return import_module("." + name, module.__name__)
	except ModuleNotFoundError:
		return getattr(module, name)


def mark_admonitions(
	app: Sphinx, what: ObjType, name: str, obj: object, options: object, lines: list[str],
) -> None:
	"""
	Add markup for admonitions (notes, tips, warnings, etc.) in docstrings
	"""
	def replace(match: re.Match[str]) -> str:
		return f".. {match.group(1)}::"
	regex = re.compile(
		r"^\s*(attention|caution|danger|error|hint|important|note|tip|warning):",
		re.I,
	)
	lines[:] = (regex.sub(replace, line) for line in lines)
