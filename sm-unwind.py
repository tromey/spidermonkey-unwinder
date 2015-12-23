import gdb
from gdb.FrameDecorator import FrameDecorator
import re

_have_unwinder = True
try:
    from gdb.unwinder import Unwinder
except ImportError:
    _have_unwinder = False
    # We need something here; it doesn't matter what as no unwinder
    # will ever be instantiated.
    Unwinder = object

def debug(something):
    # print(something)
    pass

# Must be in sync with JitFrames.cpp:SizeOfFramePrefix.
# Maps frametype enum base names to corresponding class.
SizeOfFramePrefix = {
    'JitFrame_Entry': 'EntryFrameLayout',

    'JitFrame_BaselineJS': 'JitFrameLayout',
    'JitFrame_IonJS': 'JitFrameLayout',
    'JitFrame_Bailout': 'JitFrameLayout',
    'JitFrame_Unwound_BaselineJS': 'JitFrameLayout',
    'JitFrame_Unwound_IonJS': 'JitFrameLayout',

    'JitFrame_BaselineStub': 'BaselineStubFrameLayout',
    'JitFrame_Unwound_BaselineStub': 'BaselineStubFrameLayout',

    'JitFrame_IonStub': 'JitStubFrameLayout',
    'JitFrame_Unwound_IonStub': 'JitStubFrameLayout',

    'JitFrame_Rectifier': 'RectifierFrameLayout',

    'JitFrame_Unwound_Rectifier': 'IonUnwoundRectifierFrameLayout',

    'JitFrame_Exit': 'ExitFrameLayout',
    'JitFrame_LazyLink': 'ExitFrameLayout',

    'JitFrame_IonAccessorIC': 'IonAccessorICFrameLayout',
    'JitFrame_Unwound_IonAccessorIC': 'IonAccessorICFrameLayout',
}

# All types and symbols that we need are attached to an object that we
# can dispose of as needed.
class UnwinderTypeCache(object):
    def __init__(self):
        self.FRAMETYPE_MASK = (1 << gdb.parse_and_eval('js::jit::FRAMETYPE_BITS')) - 1
        self.FRAMESIZE_SHIFT = gdb.parse_and_eval('js::jit::FRAMESIZE_SHIFT')
        self.compute_frame_size_map()
        commonFrameLayout = gdb.lookup_type('js::jit::CommonFrameLayout')
        self.typeCommonFrameLayoutPointer = commonFrameLayout.pointer()
        self.per_tls_data = gdb.lookup_global_symbol('js::TlsPerThreadData')

    # Compute map, indexed by a JitFrame value (an integer), whose
    # values are size of corresponding frame classes.
    def compute_frame_size_map(self):
        t = gdb.lookup_type('enum js::jit::FrameType')
        self.frame_size_map = {}
        self.frame_enum_values = {}
        self.frame_enum_names = {}
        for field in t.fields():
            # Strip off "js::jit::".
            name = field.name[9:]
            class_type = gdb.lookup_type('js::jit::' + SizeOfFramePrefix[name])
            self.frame_enum_values[name] = int(field.enumval)
            self.frame_enum_names[int(field.enumval)] = name
            self.frame_size_map[int(field.enumval)] = class_type.sizeof

# gdb doesn't have a direct way to tell us if a given address is
# claimed by some shared library or the executable.  See
# https://sourceware.org/bugzilla/show_bug.cgi?id=19288
# In the interest of not requiring a patched gdb, instead we read
# /proc/.../maps.  This only works locally, but maybe could work
# remotely using "remote get".  FIXME.
def parse_proc_maps():
    mapfile = '/proc/' + str(gdb.selected_inferior().pid) + '/maps'
    # Note we only examine executable mappings here.
    matcher = re.compile("^([a-fA-F0-9]+)-([a-fA-F0-9]+)\s+..x.\s+\S+\s+\S+\s+\S*(.*)$")
    mappings = []
    with open(mapfile, "r") as inp:
        for line in inp:
            match = matcher.match(line)
            if not match:
                # Header lines and such.
                continue
            start = match.group(1)
            end = match.group(2)
            name = match.group(3).strip()
            if name is '' or (name.startswith('[') and name is not '[vdso]'):
                # Skip entries not corresponding to a file.
                continue
            mappings.append((int(start, 16), int(end, 16)))
    return mappings

# This represents a single JIT frame for the purposes of display.
# That is, the frame filter creates instances of this when it sees a
# JIT frame in the stack.
class JitFrameDecorator(FrameDecorator):
    def __init__(self, base, info):
        super(JitFrameDecorator, self).__init__(base)
        self.info = info

    def function(self):
        if "name" in self.info:
            return "<<" + self.info["name"] + ">>"
        return FrameDecorator.function(self)

# A frame filter for SpiderMonkey.
class SpiderMonkeyFrameFilter(object):
    # |state_holder| is either None, or an instance of
    # SpiderMonkeyUnwinder.  If the latter, then this class will
    # reference the |unwinder_state| attribute to find the current
    # unwinder state.
    def __init__(self, state_holder):
        self.name = "SpiderMonkey"
        self.enabled = True
        self.priority = 100
        self.state_holder = state_holder

    def maybe_wrap_frame(self, frame):
        if self.state_holder is None or self.state_holder.unwinder_state is None:
            return frame
        base = frame.inferior_frame()
        info = self.state_holder.unwinder_state.get_frame(base)
        if info is None:
            return frame
        return JitFrameDecorator(frame, info)

    def filter(self, frame_iter):
        return map(self.maybe_wrap_frame, frame_iter)

# A frame id class, as specified by the gdb unwinder API.
class SpiderMonkeyFrameId(object):
    def __init__(self, sp, pc):
        self.sp = sp
        self.pc = pc

# This holds all the state needed during a given unwind.  Each time a
# new unwind is done, a new instance of this class is created.  It
# keeps track of all the state needed to unwind JIT frames.  Note that
# this class is not directly instantiated.
#
# This is a base class, and must be specialized for each target
# architecture, both because we need to use arch-specific register
# names, and because entry frame unwinding is arch-specific.
# See https://sourceware.org/bugzilla/show_bug.cgi?id=19286 for info
# about the register name issue.
#
# Each subclass must define SP_REGISTER, PC_REGISTER, and
# SENTINEL_REGISTER (see x64UnwinderState for info); and implement
# unwind_entry_frame.
class UnwinderState(object):
    def __init__(self, typecache):
        debug("@@ new UnwinderState")
        self.next_sp = None
        self.next_type = None
        self.activation = None
        # An unwinder instance is specific to a thread.  Record the
        # selected thread for later verification.
        self.thread = gdb.selected_thread()
        self.frame_map = {}
        self.proc_mappings = parse_proc_maps()
        self.typecache = typecache

    # If the given gdb.Frame was created by this unwinder, return the
    # corresponding informational dictionary for the frame.
    # Otherwise, return None.  This is used by the frame filter to
    # display extra information about the frame.
    def get_frame(self, frame):
        sp = int(frame.read_register(self.SP_REGISTER))
        if sp in self.frame_map:
            return self.frame_map[sp]
        return None

    # Add information about a frame to the frame map.  This map is
    # queried by |self.get_frame|.  |sp| is the frame's stack pointer,
    # and |dictionary| holds any extra information about the frame.
    # Currently the only defined member of the dictionary is "name",
    # which holds the frame's type as a string, e.g. "JitFrame_Exit".
    def add_frame(self, sp, dictionary):
        self.frame_map[int(sp)] = dictionary

    # See whether |pc| is claimed by some text mapping.  See
    # |parse_proc_maps| for details on how the decision is made.
    def text_address_claimed(self, pc):
        for (start, end) in self.proc_mappings:
            if (pc >= start and pc <= end):
                return True
        return False

    # Check whether |self| is valid for the selected thread.
    def check(self):
        return gdb.selected_thread() is self.thread

    # Essentially js::TlsPerThreadData.get().
    def get_tls_per_thread_data(self):
        return self.typecache.per_tls_data.value()['mValue']

    # |common| is a pointer to a CommonFrameLayout object.  Return a
    # tuple (size, frame_type), where |size| is the integer size of
    # the frame in bytes, and |frame_type| is an integer representing
    # the frame type.
    def unpack_descriptor(self, common):
        value = common['descriptor_']
        size = int(value >> self.typecache.FRAMESIZE_SHIFT)
        frame_type = int(value & self.typecache.FRAMETYPE_MASK)
        return (size, frame_type)

    # Given a frame_type, return the base size of the frame in bytes.
    def sizeof_frame_type(self, frame_type):
        return self.typecache.frame_size_map[frame_type]

    # Create a new frame for gdb.  |sp| is the stack pointer to use,
    # and |pending_frame| is the pending frame (see the gdb unwinder
    # documentation).  This uses the pending frame to make unwind
    # information for gdb.  This unwind info is returned.  This also
    # registers the newly-created frame using |self.add_frame|.
    def create_frame(self, sp, pending_frame):
        common = sp.cast(self.typecache.typeCommonFrameLayoutPointer)
        debug("@@ common = 0x%x : %s" % (int(sp), str(common.dereference())))
        new_pc = common['returnAddress_']
        frame_type = self.next_type
        (size, self.next_type) = self.unpack_descriptor(common)
        debug("@@ type = %s" % self.typecache.frame_enum_names[frame_type])
        if self.next_type == self.typecache.frame_enum_values['JitFrame_Entry']:
            # For the entry frame we don't look at the size of the
            # EntryFrameLayout, but rather CommonFrameLayout.  This
            # matches what the code in generateEnterJIT does.
            frame_size = self.typecache.typeCommonFrameLayoutPointer.target().sizeof
        else:
            frame_size = self.sizeof_frame_type(frame_type)
        self.next_sp = sp + size + frame_size
        frame_id = SpiderMonkeyFrameId(sp, new_pc)
        # Register this frame so the frame filter can find it.  This
        # would be a good spot to try to fetch the function object,
        # arguments, etc.
        self.add_frame(sp, {
            "name": self.typecache.frame_enum_names[self.next_type]
        })
        # FIXME it would be great to unwind any other registers here.
        unwind_info = pending_frame.create_unwind_info(frame_id)
        # gdb mysteriously doesn't do this automatically.
        # See https://sourceware.org/bugzilla/show_bug.cgi?id=19287
        unwind_info.add_saved_register(self.PC_REGISTER, frame_id.pc)
        unwind_info.add_saved_register(self.SP_REGISTER, frame_id.sp)
        return unwind_info

    # Unwind an "ordinary" JIT frame.  This is used for JIT frames
    # other than enter and exit frames.  Returns the newly-created
    # unwind info for gdb.
    def unwind_ordinary(self, pending_frame):
        debug("@@ unwind_ordinary")
        return self.create_frame(self.next_sp, pending_frame)

    # Unwind an exit frame.  Returns None if this cannot be done;
    # otherwise returns the newly-created unwind info for gdb.
    def unwind_exit_frame(self, pending_frame):
        if self.activation == 0:
            debug("@@ unwind_exit_frame: end")
            # Reached the end of the list.
            self.expected_sp = None
            return None
        if self.activation is None:
            debug("@@ unwind_exit_frame: first")
            ptd = self.get_tls_per_thread_data()
            self.activation = ptd['runtime_']['jitActivation']
            jittop = ptd['runtime_']['jitTop']
        else:
            debug("@@ unwind_exit_frame: next")
            jittop = self.activation['prevJitTop_']
            self.activation = self.activation['prevJitActivation_']
        debug("@@ jittop = 0x%x" % jittop)

        exit_sp = pending_frame.read_register(self.SP_REGISTER)
        self.add_frame(exit_sp, { "name": "JitFrame_Exit" })

        # Now we can just fall into the ordinary case.
        self.next_type = self.typecache.frame_enum_values['JitFrame_Exit']
        return self.create_frame(jittop, pending_frame)

    # The main entry point that is called to try to unwind a JIT frame
    # of any type.  Returns None if this cannot be done; otherwise
    # returns the newly-created unwind info for gdb.
    def unwind(self, pending_frame):
        pc = pending_frame.read_register(self.PC_REGISTER)

        # If some shared library claims this address, bail.  GDB
        # defers to our unwinder by default, but we don't really want
        # that kind of power.
        if self.text_address_claimed(int(pc)):
            return None

        if self.next_sp is not None:
            if self.next_type == self.typecache.frame_enum_values['JitFrame_Entry']:
                result = self.unwind_entry_frame(self.next_sp, pending_frame)
                self.next_sp = None
                self.next_type = None
                return result
            return self.unwind_ordinary(pending_frame)
        # Maybe we've found an exit frame.  FIXME I currently don't
        # know how to identify these precisely, so we'll just hope for
        # the time being.
        return self.unwind_exit_frame(pending_frame)

# The UnwinderState subclass for x86-64.
class x64UnwinderState(UnwinderState):
    SP_REGISTER = 'rsp'
    PC_REGISTER = 'rip'

    # A register unique to this architecture, that is also likely to
    # have been saved in any frame.  The best thing to use here is
    # some arch-specific name for PC or SP.
    SENTINEL_REGISTER = 'rip'

    # Must be in sync with Trampoline-x64.cpp:generateEnterJIT.  Note
    # that rip isn't pushed there explicitly, but rather by the
    # previous function's call.
    PUSHED_REGS = ["r15", "r14", "r13", "r12", "rbx", "rbp", "rip"]

    # Unwind an entry frame.  Returns the newly-created unwind info
    # for gdb.
    def unwind_entry_frame(self, sp, pending_frame):
        debug("@@ unwind_entry_frame")
        debug("@@ entry sp = 0x%x" % int(sp))
        void_starstar = gdb.lookup_type('void').pointer().pointer()
        sp = sp.cast(void_starstar)
        # We have to unwind the registers first, then create the frame
        # id.  So we stash the registers in a temporary dictionary
        # here.
        regs = {}
        # Skip the "result" push.
        sp = sp + 1
        for reg in self.PUSHED_REGS:
            data = sp.dereference()
            sp = sp + 1
            regs[reg] = data
            if reg is "rbp":
                regs[self.SP_REGISTER] = sp
        frame_id = SpiderMonkeyFrameId(regs[self.SP_REGISTER],
                                       regs[self.PC_REGISTER])
        unwind_info = pending_frame.create_unwind_info(frame_id)
        debug("@@ sym @ %s" % str(regs[self.PC_REGISTER]))
        for reg in regs:
            debug("@@ unwinding %s => 0x%x" % (reg, regs[reg]))
            unwind_info.add_saved_register(reg, regs[reg])
        return unwind_info

# The unwinder object.  This provides the "user interface" to the JIT
# unwinder, and also handles constructing or destroying UnwinderState
# objects as needed.
class SpiderMonkeyUnwinder(Unwinder):
    # A list of all the possible unwinders.  See |self.make_unwinder|.
    UNWINDERS = [x64UnwinderState]

    def __init__(self, typecache):
        super(SpiderMonkeyUnwinder, self).__init__("SpiderMonkey")
        self.typecache = typecache
        self.unwinder_state = None
        # We need to invalidate the unwinder state whenever the
        # inferior starts executing.  This avoids having a stale
        # cache.
        gdb.events.cont.connect(self.invalidate_unwinder_state)
        assert self.test_sentinels()

    def test_sentinels(self):
        # Self-check.
        regs = {}
        for unwinder in self.UNWINDERS:
            if unwinder.SENTINEL_REGISTER in regs:
                return False
            regs[unwinder.SENTINEL_REGISTER] = 1
        return True

    def make_unwinder(self, pending_frame):
        # gdb doesn't provide a good way to find the architecture.
        # See https://sourceware.org/bugzilla/show_bug.cgi?id=19399
        # So, we look at each known architecture and see if the
        # corresponding "unique register" is known.
        for unwinder in self.UNWINDERS:
            try:
                pending_frame.read_register(unwinder.SENTINEL_REGISTER)
            except:
                # Failed to read the register, so let's keep going.
                # This is more fragile than it might seem, because it
                # fails if the sentinel register wasn't saved in the
                # previous frame.
                continue
            return unwinder(self.typecache)
        return None

    def __call__(self, pending_frame):
        if self.unwinder_state is None or not self.unwinder_state.check():
            self.unwinder_state = self.make_unwinder(pending_frame)
        if not self.unwinder_state:
            return None
        return self.unwinder_state.unwind(pending_frame)

    def invalidate_unwinder_state(self, *args, **kwargs):
        self.unwinder_state = None

# Register the unwinder and frame filter with |objfile|.  If |objfile|
# is None, register them globally.
def register_unwinder(objfile):
    unwinder = None
    if _have_unwinder:
        unwinder = SpiderMonkeyUnwinder(UnwinderTypeCache())
        gdb.unwinder.register_unwinder(objfile, unwinder, replace=True)
    # We unconditionally register the frame filter, because at some
    # point we'll add interpreter frame filtering.
    filt = SpiderMonkeyFrameFilter(unwinder)
    if objfile is None:
        objfile = gdb
    objfile.frame_filters[filt.name] = filt

# A temporary hack.
register_unwinder(None)
