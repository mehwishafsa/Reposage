/* Arithmetic for the calculator. */
#include "calc.h"

/* Add two numbers. */
double add(double a, double b)
{
    return a + b;
}

/* Subtract b from a. */
double subtract(double a, double b)
{
    return a - b;
}

/* Multiply two numbers. */
double multiply(double a, double b)
{
    return a * b;
}

/* Divide a by b. The caller must check that b is not zero. */
double divide(double a, double b)
{
    return a / b;
}

/* Raise base to a whole-number exponent using a loop. */
double power(double base, int exponent)
{
    double result = 1;
    int i;
    for (i = 0; i < exponent; i++) {
        result = multiply(result, base);
    }
    return result;
}
