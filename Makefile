py-jit.so: py-jit.c
	gcc -std=c99 `pkg-config --cflags python` -g -fPIC -shared -o py-jit.so py-jit.c
