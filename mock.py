# Mock a pending frame for interactive testing.

class MockPendingFrame(object):
    def __init__(self, frameid = None):
        self.frame = gdb.selected_frame()
        self.frameid = frameid

    def read_register(self, name):
        if self.frameid:
            if name is 'pc' or name is 'rip':
                return self.frameid.pc
            if name is 'sp' or name is 'rsp':
                return self.frameid.sp
            raise ValueError('did not mock %s' % name)
        return self.frame.read_register(name)

    def create_unwind_info(self, frame_id):
        return MockPendingFrame(frame_id)
