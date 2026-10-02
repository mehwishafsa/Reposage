/* Array practice: sum, largest value and average. */
#include <stdio.h>
#include "stats.h"

/* Ask for up to `max` numbers and store them in the array.
   Returns how many numbers were entered. */
int read_numbers(int numbers[], int max)
{
    int count, i;
    printf("How many numbers (1-%d)? ", max);
    scanf("%d", &count);
    if (count < 1 || count > max) {
        count = max;
    }
    for (i = 0; i < count; i++) {
        printf("Number %d: ", i + 1);
        scanf("%d", &numbers[i]);
    }
    return count;
}

/* Add up all numbers in the array. */
int sum_array(int numbers[], int count)
{
    int total = 0, i;
    for (i = 0; i < count; i++) {
        total += numbers[i];
    }
    return total;
}

/* Find the largest number in the array. */
int find_max(int numbers[], int count)
{
    int best = numbers[0], i;
    for (i = 1; i < count; i++) {
        if (numbers[i] > best) {
            best = numbers[i];
        }
    }
    return best;
}

/* The average is the sum divided by how many numbers there are. */
double average(int numbers[], int count)
{
    return (double)sum_array(numbers, count) / count;
}

/* Read numbers, then print their sum, largest value and average. */
void run_stats(void)
{
    int numbers[MAX_NUMBERS];
    int count = read_numbers(numbers, MAX_NUMBERS);
    printf("Sum = %d\n", sum_array(numbers, count));
    printf("Largest = %d\n", find_max(numbers, count));
    printf("Average = %.2f\n", average(numbers, count));
}

typedef struct {
    int count;
    double mean;
} Summary;

struct Node {
    int value;
    struct Node *next;
};

/* Calls through a struct field are function pointers: not linked. */
void use_pointer(struct Ops *ops)
{
    ops->apply(1);
}
