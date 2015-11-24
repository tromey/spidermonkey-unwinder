// Run with --baseline-eager and set a breakpoint in 'Print'.

for (var i = 0; i < 5000; ++i) {
    if (i == 4700) {
	print(i);
    }
}
