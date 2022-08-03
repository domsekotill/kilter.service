SPHINXBUILD = sphinx-build
SPHINXOPTS = -j auto -E
SPHINXAPIDOC = sphinx-apidoc
SPHINXAPIDOCOPTS = --separate
BUILDDIR = build

# Alternatives: html dirhtml singlehtml
HTML_BUILDER = html

# Values above this line may be modified from cmdline
# ======

BUILD_SRC = Makefile $(wildcard *.py)
SPHINX_SRC = index.md kilter.service.rst
SPHINX_SRC += ../README.md
APIDOC_SRC := $(wildcard ../kilter/service/*.py)
APIDOC_SRC += $(wildcard templates/apidoc/*.rst_t)

BUILDOUT = $(@D)


.PHONY: default all
default:

.PHONY all default: html
html: BUILDER=$(HTML_BUILDER)
html: doctest $(BUILDDIR)/html/index.html

.PHONY all: man
man: BUILDER=man
man: doctest $(BUILDDIR)/man/kilterkilterservice.1

.PHONY all: text
text: BUILDER=text
text: doctest $(BUILDDIR)/text/index.txt

.PHONY: doctest
doctest: BUILDER=doctest
doctest: $(BUILDDIR)/doctest/output.txt $(APIDOC_SRC)

.PHONY: clean
clean:
	-rm -rf $(BUILDDIR)
	-rm kilter.*


kilter.service.rst: $(BUILD_SRC) $(APIDOC_SRC)
	$(SPHINXAPIDOC) $(SPHINXAPIDOCOPTS) --implicit-namespaces -MTft templates/apidoc -o . ../kilter
	@rm kilter.rst
	@touch $@

$(BUILDDIR)/%: $(BUILD_SRC) $(SPHINX_SRC) | $(BUILDDIR)
	$(SPHINXBUILD) -ab $(BUILDER) $(SPHINXOPTS) . $(BUILDOUT)

$(BUILDDIR):
	mkdir -p $@
