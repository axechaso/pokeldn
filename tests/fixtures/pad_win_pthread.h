// Only the pthread/time operations needed by pad_player_host.c, backed by Windows primitives.
#pragma once
#include <windows.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>
typedef SRWLOCK pthread_mutex_t;
typedef CONDITION_VARIABLE pthread_cond_t;
typedef HANDLE pthread_t;
#define PTHREAD_MUTEX_INITIALIZER SRWLOCK_INIT
#define CLOCK_REALTIME 0
#define CLOCK_MONOTONIC 1
static int clock_gettime(int clock, struct timespec *out)
{
    uint64_t ns;
    if (clock == CLOCK_MONOTONIC) {
        ns = GetTickCount64() * 1000000;
    } else {
        FILETIME ft;
        GetSystemTimePreciseAsFileTime(&ft);
        ns = (((uint64_t)ft.dwHighDateTime << 32) | ft.dwLowDateTime) * 100;
    }
    out->tv_sec = (time_t)(ns / 1000000000);
    out->tv_nsec = (long)(ns % 1000000000);
    return 0;
}
static int nanosleep(const struct timespec *delay, void *unused)
{
    Sleep((DWORD)(delay->tv_sec * 1000 + (delay->tv_nsec + 999999) / 1000000));
    return 0;
}
static int pthread_mutex_init(pthread_mutex_t *lock, void *unused) { InitializeSRWLock(lock); return 0; }
static int pthread_mutex_lock(pthread_mutex_t *lock) { AcquireSRWLockExclusive(lock); return 0; }
static int pthread_mutex_unlock(pthread_mutex_t *lock) { ReleaseSRWLockExclusive(lock); return 0; }
static int pthread_cond_init(pthread_cond_t *cv, void *unused) { InitializeConditionVariable(cv); return 0; }
static int pthread_cond_broadcast(pthread_cond_t *cv) { WakeAllConditionVariable(cv); return 0; }
static int pthread_cond_wait(pthread_cond_t *cv, pthread_mutex_t *lock)
{
    return SleepConditionVariableSRW(cv, lock, INFINITE, 0) ? 0 : 1;
}
static int pthread_cond_timedwait(pthread_cond_t *cv, pthread_mutex_t *lock, const struct timespec *until)
{
    struct timespec now;
    clock_gettime(CLOCK_REALTIME, &now);
    int64_t ns = (until->tv_sec - now.tv_sec) * 1000000000LL + until->tv_nsec - now.tv_nsec;
    DWORD ms = ns > 0 ? (DWORD)((ns + 999999) / 1000000) : 0;
    return SleepConditionVariableSRW(cv, lock, ms, 0) ? 0 : 1;
}
struct win_thread_start { void *(*fn)(void *); void *arg; };
static DWORD WINAPI win_thread_run(void *arg)
{
    struct win_thread_start start = *(struct win_thread_start *)arg;
    free(arg);
    start.fn(start.arg);
    return 0;
}
static int pthread_create(pthread_t *thread, void *unused, void *(*fn)(void *), void *arg)
{
    struct win_thread_start *start = malloc(sizeof(*start));
    *start = (struct win_thread_start){fn, arg};
    *thread = CreateThread(NULL, 0, win_thread_run, start, 0, NULL);
    return *thread ? 0 : 1;
}
static int pthread_detach(pthread_t thread) { CloseHandle(thread); return 0; }
