gdb-jit-py.so: gdb-jit-py.c
	gcc -std=c99 `pkg-config --cflags python` -g -fPIC -shared -o gdb-jit-py.so gdb-jit-py.c
