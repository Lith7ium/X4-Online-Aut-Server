#include <stdbool.h>
static bool paused=true;
static double game=100;
void TestReset(void){paused=true;game=100;}
void TestUnpause(void){paused=false;}
void TestPause(void){paused=true;}
void TestAdvance(double v){game+=v;}
bool IsPlayerValid(void){return true;}
bool IsGamePaused(void){return paused;}
double GetCurrentGameTime(void){return game;}
const char *GetSaveFolderPath(void){return "/private/45841/save";}
#include <stdint.h>
#include <string.h>
typedef uint64_t UniverseID;
typedef struct {float x,y,z,yaw,pitch,roll;} UIPosRot;
bool IsValidComponent(UniverseID v){return v==41 || v==43;}
bool IsComponentOperational(UniverseID v){return v==41;}
bool IsComponentClass(UniverseID v,const char *c){return (v==41 && !strcmp(c,"ship")) || (v==43 && !strcmp(c,"sector"));}
static UniverseID actor_sector = 43;
static UniverseID actor_zone;
static UniverseID actor_highway;
static UniverseID controlled_ship = 41;
static bool enter_highway_on_pose;
static int pose_reads;

void TestMotionContext(UniverseID sector, UniverseID zone, UniverseID highway,
                       UniverseID controlled, bool enter_during_read) {
    actor_sector = sector;
    actor_zone = zone;
    actor_highway = highway;
    controlled_ship = controlled;
    enter_highway_on_pose = enter_during_read;
}

int TestPoseReads(void) { return pose_reads; }
UniverseID GetPlayerControlledShipID(void) { return controlled_ship; }

UniverseID GetContextByClass(UniverseID actor, const char *class_name, bool recursive) {
    (void)recursive;
    if (actor != 41) return 0;
    if (strcmp(class_name, "sector") == 0) return actor_sector;
    if (strcmp(class_name, "zone") == 0) return actor_zone;
    if (strcmp(class_name, "highway") == 0) return actor_highway;
    return 0;
}

UIPosRot GetObjectPositionInSector(UniverseID actor) {
    (void)actor;
    ++pose_reads;
    if (enter_highway_on_pose) actor_sector = 0;
    return (UIPosRot){-400, 100000, 500, 0, 0, 0};
}
