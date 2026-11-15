// podstage display reserve: keeps the session's X servers off the host's
// display numbers.
//
// Symptom it fixes: a host desktop app (the ChatGPT desktop build, a browser,
// anything Electron) opens on the same screen the session streams, on top of
// the game, so it also shows up on the TV in the other room.
//
// Cause: the container runs with --network host, which shares the host's
// NETWORK namespace with it — and with it the abstract AF_UNIX namespace.
// /tmp is private to the container (no /tmp bind, only a fresh /run tmpfs), so
// the filesystem sockets /tmp/.X11-unix/X<n> never collide; Xwayland however
// also registers the abstract name @/tmp/.X11-unix/X<n>, and Xlib/libxcb tries
// the abstract transport FIRST. With nothing holding those names, the
// session's Xwayland takes :0, and every host X client that asks for :0 is
// answered by the session's X server instead of the host's: the window is
// composited by labwc/gamescope and encoded by the streamer.
//
// Fix: bind the low display names here, without listen(), before the
// compositor can spawn an X server. The session's X servers then move up to a
// number the host does not use (X servers retry the next display when the
// well-known sockets cannot be created), and a host client aimed at :0 gets
// ECONNREFUSED here and falls back to the host's filesystem socket, which is
// where the host's own X server listens.
//
// Bound without listen() on purpose: an abstract name cannot be unlinked, so
// ownership is stable for as long as this process lives, and a client that
// still connects is refused rather than talking to a socket nobody serves.
//
// A display another X server already holds is skipped, never stolen: that
// server owns the name, so the session cannot land there anyway.
//
// Usage: podstage-display-reserve [COUNT]   (default 4, i.e. :0-:3)
// Any termination signal releases every name: the sockets die with the process.
#define _GNU_SOURCE
#include <errno.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#define ABSTRACT_PREFIX "/tmp/.X11-unix/X"
#define MAX_RESERVE     64

int main(int argc, char **argv) {
    int count = 4;

    if (argc > 2) {
        fprintf(stderr, "usage: %s [count]\n", argv[0]);
        return 2;
    }
    if (argc == 2) {
        char *end = NULL;
        long n = strtol(argv[1], &end, 10);
        if (argv[1][0] == '\0' || *end != '\0' || n < 0 || n > MAX_RESERVE) {
            fprintf(stderr, "%s: bad count '%s' (0-%d)\n", argv[0], argv[1],
                    MAX_RESERVE);
            return 2;
        }
        count = (int)n;
    }

    int held = 0;
    for (int display = 0; display < count; display++) {
        char name[sizeof(ABSTRACT_PREFIX) + 8];
        int len = snprintf(name, sizeof name, ABSTRACT_PREFIX "%d", display);
        if (len < 0 || (size_t)len >= sizeof name) {
            fprintf(stderr, "display-reserve: display name too long\n");
            return 1;
        }

        struct sockaddr_un addr;
        memset(&addr, 0, sizeof addr);
        addr.sun_family = AF_UNIX;
        // sun_path[0] stays NUL: that is what makes the name abstract.
        memcpy(addr.sun_path + 1, name, (size_t)len);
        socklen_t addrlen =
            (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + (size_t)len);

        int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (fd < 0) {
            perror("display-reserve: socket");
            return 1;
        }
        if (bind(fd, (struct sockaddr *)&addr, addrlen) != 0) {
            if (errno == EADDRINUSE) {
                fprintf(stderr, "display-reserve:  :%d already taken by another X "
                                "server, leaving it alone\n", display);
                close(fd);
                continue;
            }
            perror("display-reserve: bind");
            return 1;
        }
        // Deliberately no listen(): the fd is kept open for the life of the
        // process, which is the whole reservation.
        fprintf(stderr, "display-reserve:  :%d held (not listening)\n", display);
        held++;
    }

    if (count == 0)
        fprintf(stderr, "display-reserve: nothing requested (count 0)\n");
    else if (held == 0)
        fprintf(stderr, "display-reserve: no low display left to hold, another X "
                        "server owns all %d\n", count);

    // Nothing to do but keep the fds open. Default signal actions terminate
    // the process, which is exactly how the names are released.
    for (;;)
        pause();

    return 0;
}
