/* Simple test program for assembly-level debugging. */
#include <stdio.h>

int add(int a, int b) {
    return a + b;
}

int main(void) {
    int x = 10;
    int y = 20;
    int result = add(x, y);
    printf("Result: %d\n", result);
    return 0;
}
