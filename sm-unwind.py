import gdb
import GdbJitReader
import struct
import itertools

try:
    import gdb.FrameDecorator
    FrameDecorator = gdb.FrameDecorator.FrameDecorator
except ImportError:
    # We're not going to install the filter, but we still need a
    # superclass.
    FrameDecorator = object

def get_pack_fmt(buffer)
    if len(buffer) == 4:
        fmt = 'I'
    else:
        # len == 8
        fmt = 'L'
    return fmt

def unpack_addr(reg_buffer):
    return struct.unpack_from(get_pack_fmt(reg_buffer), reg_buffer)

FRAMETYPE_MASK = (1 << gdb.parse_and_eval('js::jit::FRAMETYPE_BITS')) - 1
FRAMESIZE_SHIFT = gdb.parse_and_eval('js::jit::FRAMESIZE_SHIFT')

CalleeTokenTagMask = 3  # also hard-coded in JS
CalleeTokenMask = gdb.parse_and_eval('js::jit::CalleeTokenMask')

# Must be in sync with JitFrames.cpp:SizeOfFramePrefix.
# Maps frametype enum values to corresponding class.
SizeOfFramePrefix = {
    'JitFrame_Entry': 'EntryFrameLayout',

    'JitFrame_BaselineJS': 'JitFrameLayout',
    'JitFrame_IonJS': 'JitFrameLayout',
    'JitFrame_Bailout': 'JitFrameLayout',
    'JitFrame_Unwound_BaselineJS': 'JitFrameLayout',
    'JitFrame_Unwound_IonJS': 'JitFrameLayout',

    'JitFrame_BaselineStub': 'BaselineStubFrameLayout',
    'JitFrame_Unwound_BaselineStub': 'BaselineStubFrameLayout',
    
    'JitFrame_Rectifier': 'RectifierFrameLayout',

    'JitFrame_Unwound_Rectifier': 'IonUnwoundRectifierFrameLayout',

    'JitFrame_Exit': 'ExitFrameLayout',

    'JitFrame_IonAccessorIC': 'IonAccessorICFrameLayout',
    'JitFrame_Unwound_IonAccessorIC': 'IonAccessorICFrameLayout',
}


def init_frame_type_map():
    t = gdb.lookup_type('enum js::jit::FrameType')
    result = []
    for field in t.fields():
        result[field.enumval] = field.name
    return result

# Array mapping frametype enum values to Frame objects.
frameTypeMap = init_frame_type_map()

# Any frame we discover will be represented by an instance of this
# type.
class Frame(object):
    def __init__(self):
        self.regs = []
        self.basePC = None
        self.name = None

    def getRegisters(self):
        return self.regs

    def getFrameID(self):
        return (self.basePC, self.regs[SP])

    def getName(self):
        return self.name

    def setRegister(self, regno, val):
        self.regs[regno] = val

    def setName(self, name):
        self.name = name

    def setBasePC(self, basePC):
        self.basePC = basePC

# This is used to map a stack pointer to the name of the frame.  This
# information is used by the frame filter at display time.
class StackMap(object):
    def __init__(self):
        # FIXME this really ought to be per-thread.
        self.spmap = {}

    def record(self, frame):
        self.spmap[frame.sp] = frame

    def getFrame(self, sp):
        return self.spmap[sp]

# FIXME need intelligent lifetime management for this.
currentStackMap = StackMap()

class JitFrameDecorator(FrameDecorator):
    def __init__(self, base, jitFrame):
        super(JitFrameDecorator, self).__init__(self, base)
        self.jitFrame = jitFrame

    def function(self):
        return self.jitFrame.getName()

# FIXME - nothing instantiates this yet
class JitFrameFilter(object):
    def __init__(self, objfile):
        self.name = 'SpiderMonkey JIT'
        self.enabled = True
        self.priority = 100
        objfile.frame_filters[self.name] = self

    def maybeWrapFrame(self, frame):
        # FIXME
        if frame.mumble in currentStackMap:
            return JitFrameDecorator(frame, currentStackMap[frame.mumble])
        return frame

    def filter(self, frameIter):
        return itertools.imap(self.maybeWrapFrame)

def callee_token_to_script(token):
    tag = long(token) & CalleeTokenTagMask
    token = long(token) & CalleeTokenMask
    # FIXME
    # return Value(token).cast(gdb.lookup_type('js::JSScript').pointer())

def unwind_ordinary(pc, callbacks):
    regs = []
    sp = callbacks.get_register(SP_REGNO)
    fmt = get_pack_fmt(sp)
    sp = struct.unpack_from(fmt, sp)
    descriptor = struct.unpack_from(fmt, callbacks.read_memory(sp, size))
    regs[PC_REGNO] = callbacks.read_memory(sp + size, size)
    args_size = descriptor >> FRAMESIZE_SHIFT
    frame_type = descriptor & FRAMETYPE_MASK
    type_size = type_sizes[frame_type]
    regs[SP_REGNO] = struct.pack(fmt, sp + args_size + type_size)
    return regs

# Cache the TlsPerThreadData key.  This is initialized once per run.
# FIXME - how does this work?  Maybe recreate it on each stop with a
# special case for the infcall we need?
class CacheTlsKey(object):
    def __init__(self):
        # FIXME lang c++
        self.mkey = gdb.parse_and_eval('js::TlsPerThreadData.mKey')
        # Horrifying.  And quite hard to get nicely due to the need
        # for an infcall.  It would be much better if we were using
        # __thread instead, as gdb and glibc collude to make that
        # usable.
        ptd = gdb.parse_and_eval('__GI___pthread_getspecific(js::TlsPerThreadData.mKey)')
        ptd = ptd.cast(gdb.lookup_type('PerThreadData').pointer())
        self.jitTop = ptd['runtime']['jitTop']
        self.activation = ptd['runtime']['jitActivation']

    def getTop(self):
        return self.jitTop

    def getActivation(self):
        return self.activation

class ExitFrameState(object):
    def __init__(self, mkeyCache):
        self.activation = None
        self.jittop = None
        self.mkeyCache = mkeyCache
        # FIXME lang c++
        self.typeCommonFrameLayout = gdb.lookup_type('CommonFrameLayout')
        self.typeExitFooterFrame = gdb.lookup_type('ExitFooterFrame')

    # If this is an exit frame, return the new frame; or return None.
    def is_exit_frame(self, sp, fp):
        if self.activation == 0:
            # Reached the end of the list.
            return None
        if self.activation is None:
            top = self.mkeyCache.getTop()
        else:
            top = self.activation['prevJitTop_']
        # If TOP appears between the SP and FP, then we have an exit
        # frame.
        if sp > top or top > fp:
            return None
        if self.activation is None:
            self.activation = self.mkeyCache.getActivation()
        else:
            self.activation = self.activation['prevJitActivation_']
        # FIXME - now use CommonFrameLayout and ExitFooterFrame info
        # to make a new frame.
        frame = Frame()
        frame.setName('<<exit frame>>')
        return frame

###
#
# Handling exit frames
# jitTop marks the most recent
# to find this we need (ugh)
# ((PerThreadData*) __GI___pthread_getspecific(0))->runtime_->jitTop
# 0 == TlsPerThreadData.mKey
# ... really should use __thread here!
#
# seemingly if $rsp < jitTop < $rbp
# then we can assume we're seeing the exit frame
#
# then (CommonFrameLayout*) jittop
# and (ExitFooterFrame*)(jittop - sizeof(ExitFooterFrame))
# are useful
#
# to find the next exit frame, see JitActivation::prevJitTop_
# and JitActivation::prevJitActivation_
# there is also JSRuntime::jitActivation
# which is the next pointer for the outermost activation
#
# these form a linked list terminated with 0/0
#
###

class SpiderMonkeyUnwinder(object):
    def blah(self, callbacks):

        if self.is_trampoline(pc):
            return self.unwind_trampoline(pc, callbacks)

        return unwind_ordinary(pc, callbacks)

    def unwind(self, callbacks):
        # If the PC belongs in some existing shared library, it can't
        # be ours.
        pc = unpack_addr(callbacks.get_register(PC_REGNO))
        if gdb.solib_name(pc) is not None:
            return False
        frame = self.exitFrame.is_exit_frame(sp, fp)
        if not frame:
            frame = self.is_entry(pc, callbacks)
        if not frame:
            frame = self.is_ordinary(pc, callbacks)
        if not frame:
            return False
        self.mostRecentFrame = frame
        currentStackMap.record(frame)
        return frame.getRegisters()

    def get_frame_id(self, callbacks):
        return self.mostRecentFrame.getFrameID()
        # sp = callbacks.get_register(SP_REGNO)
        # fmt = get_register(sp)
        # sp = struct.unpack_from(fmt, sp)
        # descriptor = struct.unpack_from(fmt, callbacks.read_memory(sp, size))
        # # FIXME find start of function
        # pc = struct.unpack_from(fmt, callbacks.read_memory(sp + size, size))
        # return (pc, sp)

class x64_info(SpiderMonkeyUnwinder):
    SP_REGNO = 7
    PC_REGNO = 16

    # FIXME define other registers here.

    def is_trampoline(self, pc):
        # FIXME - we need special handling for trampoline frames
        # this is just JitRuntime:: enterJIT_ and enterBaselineJIT_
        # Maybe this can move to the base class
        return False

    def unwind_trampoline(self, pc, callbacks):
        sp = callbacks.get_register(SP_REGNO)
        wordsize = len(sp)
        sp = unpack_addr(sp)
        regs = []
        # Must be in sync with Trampoline-x64.cpp:generateEnterJIT.
        pushed_regs = [rbp, rbx, r12, r13, r14, r15]
        for reg in pushed_regs:
            sp = sp - wordsize
            regs[reg] = callbacks.read_memory(sp, wordsize)
        return regs

# FIXME
GdbJitReader.register_jit_reader(x64_info())
