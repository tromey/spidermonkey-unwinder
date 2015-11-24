GDB = gdb
JS =  /home/tromey/firefox-git/gecko/obj-x86_64-unknown-linux-gnu/dist/bin/js

check: test.js sm-unwind.py
	gdb -batch $(JS) -ex 'break Print' -ex 'source sm-unwind.py' -ex 'run --baseline-eager test.js' -ex 'bt'
