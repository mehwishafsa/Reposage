/* Statistics on a list of numbers kept in an array. */
#ifndef STATS_H
#define STATS_H

#define MAX_NUMBERS 10

void run_stats(void);
int read_numbers(int numbers[], int max);
int sum_array(int numbers[], int count);
int find_max(int numbers[], int count);
double average(int numbers[], int count);

#endif
