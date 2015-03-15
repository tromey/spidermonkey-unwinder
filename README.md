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

The function name, and perhaps other information, will be displayed
using a frame filter.

# GDB

Writing a jit symbol reader is a pain: the current gdb jit interface
admits the possibility of reading symbols from the inferior.  However,
this is done in response to some inferior event.  It would be much
better if it were possible to read symbols on demand instead -- that
is, in response to a user action in gdb.

# To Do

* Need a simple architecture abstraction to hold the register numbers
  and any other per-arch bits

* Need a way to detect the trampoline frames used to enter JIT code.
  These are made by `generateEnterJIT`, and there seem to be just two:
  `JitRuntime::enterJIT_` and `JitRuntime::enterBaselineJIT_`.

* Need a way to compute the frame pointer for the newest JIT frame on
  the stack.  We can maybe stash it in `JSRuntime` in a special debug
  mode?  Something like this is done for exit frames, see
  `JSRuntime::jitTop`.  Basically we need either a frame descriptor
  emitted by the JIT, or a frame pointer of some kind.  As a hack we
  could require the user to use some `set` command and determine this
  by hand.

* Need a type cache for some types in the unwinder.  There's one in
  the existing gdb scripts in js.

* We may need to keep some state to handle exit frames properly.  Only
  the newest SP is held in `jitTop`; the rest are in a list of
  `JitActivation` objects.  In this case we can cache the state by
  thread; and clear the entire cache whenever `gdb.events.cont` emits
  an event.

* It would be really good to be able to find all the saved registers
  in ordinary JIT frames.  I'm not sure if this is reasonably possible.

* We should at least consider what happens if gdb stops in a function
  prologue.
