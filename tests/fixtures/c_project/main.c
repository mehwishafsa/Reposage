/* Simple calculator lab program.
   Shows a menu, reads two numbers and prints the result,
   until the user chooses 0 to quit. */
#include <stdio.h>
#include "lib/calc.h"
#include "stats.h"

/* Print the list of choices. */
void show_menu(void)
{
    printf("\n1) add  2) subtract  3) multiply  4) divide  5) power  6) stats  0) quit\n");
    printf("Your choice: ");
}

/* Run one calculation for the chosen operation. */
void run_choice(int choice)
{
    double a, b;
    if (choice == 6) {
        run_stats();
        return;
    }
    printf("Enter two numbers: ");
    scanf("%lf %lf", &a, &b);

    switch (choice) {
    case 1: printf("Result = %.2f\n", add(a, b)); break;
    case 2: printf("Result = %.2f\n", subtract(a, b)); break;
    case 3: printf("Result = %.2f\n", multiply(a, b)); break;
    case 4:
        if (b == 0) {
            printf("Cannot divide by zero!\n");
        } else {
            printf("Result = %.2f\n", divide(a, b));
        }
        break;
    case 5: printf("Result = %.2f\n", power(a, (int)b)); break;
    default: printf("Unknown choice\n");
    }
}

int main(void)
{
    int choice = -1;
    while (choice != 0) {
        show_menu();
        scanf("%d", &choice);
        if (choice != 0) {
            run_choice(choice);
        }
    }
    printf("Bye!\n");
    return 0;
}
