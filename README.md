# Intro

This is a gdb JIT unwinder for SpiderMonkey.

It does not work yet.

SpiderMonkey's JIT compilers emit code at runtime that cannot be
unwound by gdb.  So, for example `bt` does not work properly.  This
code attempts to fix this problem.

Conceptually, there are two issues to be solved.  First, the raw
unwinding -- teaching gdb how to work its way up through the frames.
Second, displaying information about the frames.

# Unwinding

Because SpiderMonkey has many frame types, and because some of the
decoding requires easy access to symbols and types -- things that are
not available to ordinary jit unwinders in gdb -- this project has a
shim to let a gdb JIT unwinder be written in Python.  Then, the
SpiderMonkey unwinder is written in Python.

# Display

My current plan is to display a function name using a frame filter.
This work is not even started

Writing a jit symbol reader is a pain: the current gdb jit interface
admits the possibility of reading symbols from the inferior.  However,
this is done in response to some inferior event.  It would be much
better if it were possible to read symbols on demand instead -- that
is, in response to a user action in gdb.
