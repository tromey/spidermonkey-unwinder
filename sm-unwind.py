import gdb
from gdb.FrameDecorator import FrameDecorator

_have_unwinder = True
try:
    from gdb.unwinder import Unwinder
except ImportError:
    _have_unwinder = False
    # We need something here.
    Unwinder = object

def debug(something):
    # print(something)
    pass

# FIXME should come from a cache
FRAMETYPE_MASK = (1 << gdb.parse_and_eval('js::jit::FRAMETYPE_BITS')) - 1
FRAMESIZE_SHIFT = gdb.parse_and_eval('js::jit::FRAMESIZE_SHIFT')

# Must be in sync with JitFrames.cpp:SizeOfFramePrefix.
# Maps frametype enum base names to corresponding class.
SizeOfFramePrefix = {
    # For the entry frame we don't look at the size of the
    # EntryFrameLayout, but rather CommonFrameLayout.  This matches
    # what the code in generateEnterJIT does.
    'JitFrame_Entry': 'CommonFrameLayout',

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

frame_enum_values = None
frame_size_map = None
frame_enum_names = None

# Compute map, indexed by a JitFrame value (an integer), whose
# values are size of corresponding frame classes.
# FIXME caching
def compute_frame_size_map():
    global SizeOfFramePrefix
    global frame_size_map
    global frame_enum_values
    global frame_enum_names
    t = gdb.lookup_type('enum js::jit::FrameType')
    frame_size_map = {}
    frame_enum_values = {}
    frame_enum_names = {}
    for field in t.fields():
        # Strip off "js::jit::".
        name = field.name[9:]
        class_type = gdb.lookup_type('js::jit::' + SizeOfFramePrefix[name])
        frame_enum_values[name] = int(field.enumval)
        frame_enum_names[int(field.enumval)] = name
        frame_size_map[int(field.enumval)] = class_type.sizeof

compute_frame_size_map()

# FIXME another cache candidate.
per_tls_data = gdb.lookup_global_symbol('js::TlsPerThreadData')

class JitFrameDecorator(FrameDecorator):
    def __init__(self, base, info):
        super(JitFrameDecorator, self).__init__(base)
        self.info = info

    def function(self):
        if "name" in self.info:
            return "<<" + self.info["name"] + ">>"
        return FrameDecorator.function(self)

class JitFrameFilter(object):
    name = "SpiderMonkey"
    enabled = True
    priority = 100

    def maybe_wrap_frame(self, frame):
        if unwinder_state is None:
            return frame
        base = frame.inferior_frame()
        info = unwinder_state.get_frame(base)
        if info is None:
            return frame
        return JitFrameDecorator(frame, info)

    def filter(self, frame_iter):
        return map(self.maybe_wrap_frame, frame_iter)

class SpiderMonkeyFrameId(object):
    def __init__(self, sp, pc):
        self.sp = sp
        self.pc = pc

class UnwinderState(object):
    # We have to use the arch-specific register names.
    # See https://sourceware.org/bugzilla/show_bug.cgi?id=19286
    # So, each subclass must define SP_REGISTER and PC_REGISTER and
    # implement unwind_entry_frame.

    def __init__(self):
        debug("@@ new UnwinderState")
        global frame_enum_values
        self.next_sp = None
        self.next_type = None
        self.activation = None
        self.thread = gdb.selected_thread()
        self.frame_map = {}
        # FIXME cache
        commonFrameLayout = gdb.lookup_type('js::jit::CommonFrameLayout')
        self.typeCommonFrameLayoutPointer = commonFrameLayout.pointer()

    def get_frame(self, frame):
        sp = int(frame.read_register(self.SP_REGISTER))
        if sp in self.frame_map:
            return self.frame_map[sp]
        return None

    def add_frame(self, sp, dictionary):
        self.frame_map[int(sp)] = dictionary

    def check(self):
        return gdb.selected_thread() is self.thread

    def get_tls_per_thread_data(self):
        global per_tls_data
        return per_tls_data.value()['mValue']

    def unpack_descriptor(self, common):
        value = common['descriptor_']
        size = int(value >> FRAMESIZE_SHIFT)
        frame_type = int(value & FRAMETYPE_MASK)
        return (size, frame_type)

    def sizeof_frame_type(self, frame_type):
        global frame_size_map
        return frame_size_map[frame_type]

    def create_frame(self, sp, pending_frame):
        common = sp.cast(self.typeCommonFrameLayoutPointer)
        debug("@@ common = 0x%x : %s" % (int(sp), str(common.dereference())))
        new_pc = common['returnAddress_']
        frame_type = self.next_type
        (size, self.next_type) = self.unpack_descriptor(common)
        debug("@@ type = %s" % frame_enum_names[frame_type])
        self.next_sp = sp + size + self.sizeof_frame_type(frame_type)
        frame_id = SpiderMonkeyFrameId(sp, new_pc)
        # Register this frame so the frame filter can find it.
        self.add_frame(sp, { "name": frame_enum_names[self.next_type] })
        # FIXME it would be great to unwind any other registers here.
        unwind_info = pending_frame.create_unwind_info(frame_id)
        # gdb mysteriously doesn't do this automatically.
        # See https://sourceware.org/bugzilla/show_bug.cgi?id=19287
        unwind_info.add_saved_register(self.PC_REGISTER, frame_id.pc)
        unwind_info.add_saved_register(self.SP_REGISTER, frame_id.sp)
        return unwind_info

    def unwind_ordinary(self, pending_frame):
        debug("@@ unwind_ordinary")
        return self.create_frame(self.next_sp, pending_frame)

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
        self.next_type = frame_enum_values['JitFrame_Exit']
        return self.create_frame(jittop, pending_frame)

    def unwind(self, pending_frame):
        pc = pending_frame.read_register(self.PC_REGISTER)

        # If some shared library claims this address, bail.  GDB
        # defers to our unwinder by default, but we don't really want
        # that kind of power.
        # FIXME this does not actually work
        # See https://sourceware.org/bugzilla/show_bug.cgi?id=19288
        if gdb.text_address_claimed(int(pc)):
            debug("@@ early exit: %s" % gdb.solib_name(int(pc)))
            return None

        if self.next_sp is not None:
            if self.next_type == frame_enum_values['JitFrame_Entry']:
                result = self.unwind_entry_frame(self.next_sp, pending_frame)
                self.next_sp = None
                self.next_type = None
                return result
            return self.unwind_ordinary(pending_frame)
        # Maybe we've found an exit frame.  FIXME I currently don't
        # know how to identify these precisely, so we'll just hope for
        # the time being.
        return self.unwind_exit_frame(pending_frame)

class x64UnwinderState(UnwinderState):
    # FIXME this means we need per-arch subclasses of UnwinderState.
    # (But we need that anyway for trampoline frames)
    SP_REGISTER = 'rsp'
    PC_REGISTER = 'rip'
    # Must be in sync with Trampoline-x64.cpp:generateEnterJIT.  Note
    # that rip isn't pushed there explicitly, but rather by the
    # previous function's call.
    PUSHED_REGS = ["r15", "r14", "r13", "r12", "rbx", "rbp", "rip"]

    def unwind_entry_frame(self, sp, pending_frame):
        debug("@@ unwind_entry_frame")
        debug("@@ entry sp = 0x%x" % int(sp))
        void_starstar = gdb.lookup_type('void').pointer().pointer()
        sp = sp.cast(void_starstar)
        # We have to unwind the registers first, then create the frame
        # id.  So we have to stash the registers in a temporary
        # dictionary here.
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

unwinder_state = None

class SpiderMonkeyUnwinder(Unwinder):
    def __init__(self):
        super(SpiderMonkeyUnwinder, self).__init__("SpiderMonkey")

    def __call__(self, pending_frame):
        global unwinder_state
        if unwinder_state is None or not unwinder_state.check():
            unwinder_state = x64UnwinderState()
        return unwinder_state.unwind(pending_frame)

def invalidate_unwinder_state(*args, **kwargs):
    unwinder_state = None

# FIXME - should register with the objfile (or wherever SpiderMonkey
# pretty-printers go)
if _have_unwinder:
    # We need to invalidate the unwinder state whenever the inferior
    # starts executing.  This avoids having a stale cache.
    gdb.events.cont.connect(invalidate_unwinder_state)
    gdb.unwinder.register_unwinder(None, SpiderMonkeyUnwinder())
    filt = JitFrameFilter()
    gdb.frame_filters[filt.name] = filt
