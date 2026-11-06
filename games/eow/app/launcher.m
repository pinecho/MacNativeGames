// The launcher inside the packaged app (scripts/build_app.py compiles it as Contents/MacOS/EoW).
// The game itself is Contents/Resources/eow/build/eow-native; everything it writes goes to the player's own folders:
//   ~/Library/Application Support/MacNativeGames/EoW/   profile (account, saves), shader cache, game slot, RomFS proof
//   ~/Library/Logs/MacNativeGames/eow.log               this run, only with the debug log on (eow.previous.log: the one before)
// The language follows the app's first preferred language:
//   defaults write org.macnativegames.eow AppleLanguages -array zh-Hans
#import <AppKit/AppKit.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "game_version.h"
#include "dmsh_chain.h"

static void show_error(NSString *detail) {
    NSAlert *alert = [NSAlert new];
    alert.messageText = @"The game could not start";
    alert.informativeText = detail;
    [alert runModal];
}

static BOOL make_dir(NSString *path) {
    return [NSFileManager.defaultManager createDirectoryAtPath:path withIntermediateDirectories:YES attributes:nil error:nil];
}

int main(int argc, char **argv) {
    @autoreleasepool {
        NSString *root = [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"eow"];
        NSString *binary = [root stringByAppendingPathComponent:@"build/eow-native"];
        /* --check (scripts/build_app.py, once after packaging): macOS scans a new app on its first run, which takes a while
           with nothing on screen; doing it during the build makes the player's first start immediate. */
        if (argc > 1 && !strcmp(argv[1], "--check")) {
            int ok = access(binary.fileSystemRepresentation, X_OK) == 0;
            NSString *declared = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
            ok = ok && [declared isEqualToString:@TOTK_GAME_VERSION];
            printf("%s: game version %s\n", ok ? "ok" : "missing binary or version mismatch", TOTK_GAME_VERSION);
            return ok ? 0 : 1;
        }
        if (access(binary.fileSystemRepresentation, X_OK) != 0) {
            show_error(@"The game files are missing from this app. Build it again with scripts/build_app.py.");
            return 1;
        }
        NSString *home = NSHomeDirectory();
        NSString *data = [home stringByAppendingPathComponent:@"Library/Application Support/MacNativeGames/EoW"];
        NSString *logs = [home stringByAppendingPathComponent:@"Library/Logs/MacNativeGames"];
        NSString *(^env_or)(const char *, NSString *) = ^NSString *(const char *name, NSString *fallback) {
            const char *v = getenv(name); return v && *v ? @(v) : fallback; };
        NSString *profile = env_or("TOTK_PROFILE_DIR", [data stringByAppendingPathComponent:@"profile"]);
        NSString *slot = env_or("TOTK_GAME_SLOT_DIR", [data stringByAppendingPathComponent:@"game-slot"]);
        NSString *cache = env_or("TOTK_SHADER_CACHE", [data stringByAppendingPathComponent:@"shader-cache/rt-" DMSH_CHAIN]);
        if (!make_dir(profile) || !make_dir(slot) || !make_dir(cache) || !make_dir(logs)) {
            show_error([NSString stringWithFormat:@"Cannot create %@.", data]);
            return 1;
        }
        if (chdir(root.fileSystemRepresentation) != 0) {   // the game reads its data relative to this folder
            show_error(@"Cannot open the game folder inside the app.");
            return 1;
        }
        setenv("TOTK_CONTROLS", env_or("TOTK_CONTROLS", [data stringByAppendingPathComponent:@"controls.txt"]).fileSystemRepresentation, 1);
        setenv("TOTK_PROFILE_DIR", profile.fileSystemRepresentation, 1);
        setenv("TOTK_SHADER_CACHE", cache.fileSystemRepresentation, 1);
        setenv("TOTK_GAME_SLOT_DIR", slot.fileSystemRepresentation, 1);
        setenv("TOTK_ROMFS_PROOF_DIR", data.fileSystemRepresentation, 1);   // the app bundle stays read-only
        setenv("TOTK_SHARED_FONTS", "extracted/firmware/shared-fonts", 1);
        setenv("DM_SHADER_LIBS_RECORD", "0", 1);                            // it would write into the bundle
        NSString *preferred = NSLocale.preferredLanguages.firstObject;
        if (preferred) setenv("TOTK_LOCALE", preferred.UTF8String, 1);
        setenv("TOTK_GPU_ALLOW_SUBMIT", "1", 1);
        setenv("TOTK_GPU_BLOCKRT", "1", 1);
        setenv("TOTK_GPU_MIDCLEAR", "1", 1);
        setenv("TOTK_GPU_WIDEN", "1", 1);
        setenv("TOTK_GPU_BUDGET_MB", "5000", 0);
        setenv("DM_PRESENT_LINK_FALLBACK", "1", 1);
        setenv("TOTK_SLOT_OWNER", "user", 0);
        setenv("TOTK_SLOT_PURPOSE", "player", 0);
        /* The log is written only with debug on: defaults write org.macnativegames.eow TOTK_DEBUG_LOG -bool YES, or
           TOTK_DEBUG_LOG=1 in the environment, which wins. Off, the game's output goes to /dev/null. */
        const char *debugEnv = getenv("TOTK_DEBUG_LOG");
        BOOL debugLog = debugEnv ? (*debugEnv && strcmp(debugEnv, "0")) : [NSUserDefaults.standardUserDefaults boolForKey:@"TOTK_DEBUG_LOG"];
        NSString *log = [logs stringByAppendingPathComponent:@"eow.log"];
        NSString *previous = [logs stringByAppendingPathComponent:@"eow.previous.log"];
        [NSFileManager.defaultManager removeItemAtPath:previous error:nil];
        if (debugLog) [NSFileManager.defaultManager moveItemAtPath:log toPath:previous error:nil];
        else [NSFileManager.defaultManager removeItemAtPath:log error:nil];
        FILE *lf = fopen(debugLog ? log.fileSystemRepresentation : "/dev/null", "w");
        if (lf) {
            dup2(fileno(lf), STDOUT_FILENO);
            dup2(fileno(lf), STDERR_FILENO);
            fclose(lf);
        }
        char *const arguments[] = {(char *)binary.fileSystemRepresentation, "rewritten/" TOTK_GAME_DIR, "boot", NULL};
        execv(arguments[0], arguments);
        int failed = errno;
        fprintf(stderr, "[launcher] execv failed: %s\n", strerror(failed));
        show_error([NSString stringWithFormat:@"Starting the game failed: %s.", strerror(failed)]);
        return 1;
    }
}
