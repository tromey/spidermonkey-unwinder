# Intro

This is a gdb JIT unwinder for SpiderMonkey.

It does not work yet.

SpiderMonkey's JIT compilers emit code at runtime that cannot be
unwound by gdb.  So, for example `bt` does not work properly.  This
code attempts to fix this problem.

Conceptually, there are two issues to be solved.  First, the raw
unwinding -- teaching gdb how to work its way up through the frames.
Second, displaying information about the frames.

# SpiderMonkey Changes

I would like to make this work without any SpiderMonkey changes.
However, currently one change is needed -- I changed some
`ThreadLocal` objects (all of them, but really only
`js::TlsPerThreadData` is needed) to use `__thread`.  This avoids an
inferior call during unwinding, which would be problematic.

# Unwinding

This work relies on the Python unwinding support that was added in
GDB 7.10.

# Display

The function name, and perhaps other information, will be displayed
using a frame filter.

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
  emitted by the JIT, or a frame pointer of some kind.  Maybe
  `ProfilingFrameIterator` would work for this.  As a hack we could
  require the user to use some `set` command and determine this by
  hand.

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
