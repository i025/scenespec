/* Native editor only: fixed text reads, immutable executables, no arbitrary programs. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <linux/audit.h>
#include <sched.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <unistd.h>

/* Linux 5.15 Landlock ABI 1; this server's older libc headers omit the UAPI. */
#define SYS_landlock_create_ruleset 444
#define SYS_landlock_add_rule 445
#define SYS_landlock_restrict_self 446
#define LANDLOCK_ACCESS_FS_EXECUTE 1ULL
#define LANDLOCK_RULE_PATH_BENEATH 1
struct landlock_ruleset_attr {uint64_t handled_access_fs;};
struct landlock_path_beneath_attr {uint64_t allowed_access;int32_t parent_fd;} __attribute__((packed));

static void fail(const char *s) { perror(s); exit(125); }
static void limit(int kind, rlim_t value) {
    struct rlimit r = {value,value};
    if (setrlimit(kind,&r)) fail("setrlimit");
}
static void executable(int rules, const char *name) {
    int fd = open(name,O_PATH|O_CLOEXEC);
    if (fd<0) fail(name);
    struct landlock_path_beneath_attr rule = {.allowed_access=LANDLOCK_ACCESS_FS_EXECUTE,.parent_fd=fd};
    if (syscall(SYS_landlock_add_rule,rules,LANDLOCK_RULE_PATH_BENEATH,&rule,0)) fail("landlock add");
    close(fd);
}
static void launch(int argc,char **argv) {
    cpu_set_t available,one;
    if (sched_getaffinity(0,sizeof(available),&available)) fail("affinity");
    CPU_ZERO(&one);
    for (int i=0;i<CPU_SETSIZE;i++) if (CPU_ISSET(i,&available)) {CPU_SET(i,&one);break;}
    if (sched_setaffinity(0,sizeof(one),&one)) fail("affinity install");
    /* V8 reserves a virtual address cage; this is AS, not physical RSS. */
    limit(RLIMIT_AS,16ULL*1024*1024*1024);
    limit(RLIMIT_CPU,45);
    limit(RLIMIT_FSIZE,8*1024*1024);
    limit(RLIMIT_CORE,0);
    limit(RLIMIT_NOFILE,128);
    limit(RLIMIT_NPROC,4096); /* Shared host UID count; NOT a per-container PID claim. */
    if (prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0)) fail("no_new_privs");
    struct landlock_ruleset_attr attr = {.handled_access_fs=LANDLOCK_ACCESS_FS_EXECUTE};
    int rules = syscall(SYS_landlock_create_ruleset,&attr,sizeof(attr),0);
    if (rules<0) fail("Landlock execution isolation unavailable");
    executable(rules,"/cli");
    executable(rules,"/codex-code-mode-host");
    executable(rules,"/bin/bash");
    executable(rules,"/bin/sh");
    if (syscall(SYS_landlock_restrict_self,rules,0)) fail("landlock restrict");
    close(rules);
    struct sock_filter filter[] = {
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,arch)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AUDIT_ARCH_X86_64,1,0),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,nr)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_sched_setaffinity,0,1),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|EPERM),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW)
    };
    struct sock_fprog prog = {.len=sizeof(filter)/sizeof(filter[0]),.filter=filter};
    if (prctl(PR_SET_SECCOMP,SECCOMP_MODE_FILTER,&prog)) fail("affinity filter");
    if (argc<3) exit(125);
    execv("/cli",argv+2);
    fail("cli");
}
static int permitted(const char *name) {
    if (!strncmp(name,"/workspace/",11)) name+=11;
    if (!strncmp(name,"./",2)) name+=2;
    return !strcmp(name,"algorithm.py") || !strcmp(name,"TASK.json") || !strcmp(name,"CONTRACT.md");
}
int main(int argc,char **argv) {
    if (argc>1 && !strcmp(argv[1],"--launch")) launch(argc,argv);
    /* A fixed reader, not a shell interpreter. Every command child is fork-free. */
    limit(RLIMIT_NPROC,0);limit(RLIMIT_AS,16*1024*1024);limit(RLIMIT_CPU,2);
    const char *command=NULL;
    for (int i=1;i+1<argc;i++) if (!strcmp(argv[i],"-c") || !strcmp(argv[i],"-lc")) command=argv[i+1];
    if (!command || strlen(command)>4096) return 126;
    if (!strcmp(command,"pwd")) {puts("/workspace");return 0;}
    if (!strcmp(command,"ls") || !strcmp(command,"ls -la") || !strcmp(command,"rg --files")) {
        puts("algorithm.py\nTASK.json\nCONTRACT.md");return 0;
    }
    char *copy=strdup(command),*save=NULL,*token=strtok_r(copy," \t\r\n",&save);
    if (!token || strcmp(token,"cat")) {
        fputs("Editor permits only pwd, ls, rg --files, and cat of the three workspace files. Use the native apply_patch tool for edits. Candidate Python runs only in the external evaluator.\n",stderr);
        return 126;
    }
    int files=0;
    while ((token=strtok_r(NULL," \t\r\n",&save))) {
        if (!permitted(token)) {fputs("File is outside the editor allowlist\n",stderr);return 126;}
        const char *base=strrchr(token,'/');base=base?base+1:token;
        char path[128];snprintf(path,sizeof(path),"/workspace/%s",base);
        int fd=open(path,O_RDONLY|O_NOFOLLOW);
        if (fd<0) fail("read workspace file");
        char buffer[4096];ssize_t size;
        while ((size=read(fd,buffer,sizeof(buffer)))>0) {
            char *b=buffer;
            while (size>0) {ssize_t n=write(1,b,size);if(n<0)fail("write");b+=n;size-=n;}
        }
        close(fd);files++;
    }
    free(copy);return files?0:126;
}
