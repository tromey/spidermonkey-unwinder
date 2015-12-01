# Intro

This is a gdb JIT unwinder for SpiderMonkey.  It can unwind at least
baseline frames, plus their entries and exits.

SpiderMonkey's JIT compilers emit code at runtime that cannot be
unwound by gdb.  So, for example `bt` does not work properly.  This
code attempts to fix this problem.

Conceptually, there are two issues to be solved.  First, the raw
unwinding -- teaching gdb how to work its way up through the frames.
Second, displaying information about the frames.

# Example

Here are some frames from the included test case, showing what it
looks like today:

```
...
#5  0x00007ffff7fe8e55 in <<JitFrame_Exit>> ()
#6  0x00007ffff7ff1c43 in <<JitFrame_BaselineStub>> ()
#7  0x00007ffff7ff2ad7 in <<JitFrame_BaselineJS>> ()
#8  0x00007ffff7fe7d5f in <<JitFrame_Entry>> ()
#9  0x00000000005ada11 in EnterBaseline(JSContext*, js::jit::EnterJitData&) (cx=cx@entry=0x1ac3990, data=...) at /home/tromey/firefox-git/gecko/js/src/jit/BaselineJIT.cpp:128
...
```

# Requirements

See below for requirements.  Currently patches to both gdb and
SpiderMonkey are needed; ask me about them.  Also, this was written
using a gdb built with Python 3 -- it can be ported to Python 2 but I
have not done so.

# Unwinding

This work relies on the Python unwinding support that was added in GDB
7.10.  It also needs a way to tell whether a given PC is already
covered by some existing object.

See https://sourceware.org/bugzilla/show_bug.cgi?id=19288

# Display

The function name, and perhaps other information, are displayed using
a frame filter.  Currently this just shows the frame type, but not the
name or anything else.

# SpiderMonkey Changes

I would like to make this work without any SpiderMonkey changes.
However, currently one change is needed -- I changed some
`ThreadLocal` objects (all of them, but really only
`js::TlsPerThreadData` is needed) to use `__thread`.  This avoids an
inferior call during unwinding, which would be problematic.

See https://bugzilla.mozilla.org/show_bug.cgi?id=757969

# To Do

* Filters and unwinders are registered globally; but when this is
  merged into SpiderMonkey we can fix that up

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

* It would be really good to be able to find all the saved registers
  in ordinary JIT frames.  I'm not sure if this is reasonably possible.

* We should at least consider what happens if gdb stops in a function
  prologue.
