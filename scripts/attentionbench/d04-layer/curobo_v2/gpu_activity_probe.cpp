// Interface test instrumentation: record public-call/GPU timestamps only.
// No solver symbols, kernel names, coefficients, or algorithm state are read.
#include <cupti.h>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <signal.h>
#include <thread>
#include <unistd.h>
#include <time.h>

namespace {
int evidence_fd=-1, signal_fd=-1;
std::atomic<int> public_active{0};
static_assert(std::atomic<int>::is_always_lock_free&&std::atomic<uint64_t>::is_always_lock_free,
              "Signal evidence requires lock-free atomics");
std::atomic<bool> running{false};
std::atomic<uint64_t> public_start{0}, public_end{0};
std::thread flusher;
struct CancelRecord { uint64_t magic, monotonic_ns, public_start_ns; int32_t public_active, signal_number; };
uint64_t monotonic_ns(){ timespec t{};clock_gettime(CLOCK_MONOTONIC,&t);return uint64_t(t.tv_sec)*1000000000ULL+t.tv_nsec; }
void terminate_record(int sig){
    CancelRecord record{0x435550544943414eULL,monotonic_ns(),public_start.load(std::memory_order_relaxed),public_active,sig};
    if(signal_fd>=0){auto written=write(signal_fd,&record,sizeof(record));(void)written;}
    _exit(128+sig);
}
void append(const char*type,uint64_t start,uint64_t end,uint64_t observed,int active,int code){
    char line[512];int count=snprintf(line,sizeof(line),"{\"event\":\"%s\",\"start_ns\":%llu,\"end_ns\":%llu,\"observed_monotonic_ns\":%llu,\"public_active\":%d,\"code\":%d}\n",type,(unsigned long long)start,(unsigned long long)end,(unsigned long long)observed,active,code);
    if(evidence_fd>=0&&count>0&&size_t(count)<sizeof(line)){auto written=write(evidence_fd,line,count);(void)written;}
}
void CUPTIAPI request_buffer(uint8_t** buffer,size_t* size,size_t* records){
    *size=1024*1024;*records=0;*buffer=nullptr;
    if(posix_memalign(reinterpret_cast<void**>(buffer),8,*size)!=0)*size=0;
}
void CUPTIAPI complete_buffer(CUcontext context,uint32_t stream,uint8_t* buffer,size_t,size_t valid){
    CUpti_Activity* record=nullptr;CUptiResult status;
    while((status=cuptiActivityGetNextRecord(buffer,valid,&record))==CUPTI_SUCCESS){
        if(record->kind!=CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL&&record->kind!=CUPTI_ACTIVITY_KIND_KERNEL)continue;
        auto* kernel=reinterpret_cast<CUpti_ActivityKernel9*>(record);
        uint64_t begin=public_start.load(),finish=public_end.load();
        if(begin&&kernel->start>=begin&&kernel->end>=kernel->start&&kernel->end!=0&&(!finish||kernel->end<=finish))
            append("completed_gpu_kernel",kernel->start,kernel->end,monotonic_ns(),public_active,0);
    }
    size_t dropped=0;auto drop_status=cuptiActivityGetNumDroppedRecords(context,stream,&dropped);
    if(dropped||drop_status!=CUPTI_SUCCESS)append("dropped_records",dropped,0,monotonic_ns(),public_active,int(drop_status));
    if(status!=CUPTI_ERROR_MAX_LIMIT_REACHED)append("parse_error",0,0,monotonic_ns(),public_active,int(status));
    free(buffer);
}
}

extern "C" int interface_gpu_probe_initialize(const char* path,const char* canceled_path){
    if(evidence_fd>=0)return -1;
    evidence_fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_APPEND,0600);
    signal_fd=open(canceled_path,O_WRONLY|O_CREAT|O_EXCL|O_APPEND,0600);
    if(evidence_fd<0||signal_fd<0)return -2;
    auto result=cuptiActivityRegisterCallbacks(request_buffer,complete_buffer);
    if(result!=CUPTI_SUCCESS)return int(result);
    result=cuptiActivityEnable(CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL);
    if(result!=CUPTI_SUCCESS)return int(result);
    struct sigaction action{};action.sa_handler=terminate_record;sigemptyset(&action.sa_mask);
    if(sigaction(SIGTERM,&action,nullptr)!=0)return -3;
    running=true;
    flusher=std::thread([]{
        while(running){
            if(public_active){auto r=cuptiActivityFlushAll(0);if(r!=CUPTI_SUCCESS)append("flush_error",0,0,monotonic_ns(),public_active,int(r));}
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
    });
    append("initialized",0,0,monotonic_ns(),0,0);return 0;
}
extern "C" int interface_gpu_probe_begin(){
    uint64_t stamp=0;auto result=cuptiGetTimestamp(&stamp);
    if(result!=CUPTI_SUCCESS)return int(result);
    public_end=0;public_start=stamp;public_active=1;
    append("public_api_begin",stamp,0,monotonic_ns(),1,0);return 0;
}
extern "C" int interface_gpu_probe_end(){
    public_active=0;uint64_t stamp=0;auto result=cuptiGetTimestamp(&stamp);public_end=stamp;
    append("public_api_end",public_start.load(),stamp,monotonic_ns(),0,int(result));
    cuptiActivityFlushAll(0);return int(result);
}
extern "C" int interface_gpu_probe_close(){
    public_active=0;running=false;if(flusher.joinable())flusher.join();
    auto result=cuptiActivityDisable(CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL);
    cuptiActivityFlushAll(0);return int(result);
}
