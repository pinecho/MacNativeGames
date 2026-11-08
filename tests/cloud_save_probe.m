/* Execute the real folder-sync code against synthetic saves in a temporary directory. */
#include "../games/totk/runtime/cloud_save.m"
void runtime_boot_status(const char *stage,const char *detail){(void)stage;(void)detail;}
int runtime_fs_save_busy(void){return 0;}
int main(int argc,char **argv){
 @autoreleasepool {
  if(argc!=5)return 2;
  NSString *root=@(argv[1]), *profile=@(argv[2]), *uid=@(argv[3]), *mode=@(argv[4]);
  NSString *local=[[profile stringByAppendingPathComponent:@"save"] stringByAppendingPathComponent:uid];
  NSFileManager *fm=NSFileManager.defaultManager;
  [fm createDirectoryAtPath:local withIntermediateDirectories:YES attributes:nil error:nil];
  NSString *progress=[local stringByAppendingPathComponent:@"progress.sav"];
  BOOL pushes=[mode isEqualToString:@"push"] || [mode isEqualToString:@"push-changed"];
  NSString *text=[mode isEqualToString:@"push"]?uid:[mode isEqualToString:@"push-changed"]?[uid stringByAppendingString:@" changed"]:@"local copy";
  [text writeToFile:progress atomically:YES encoding:NSUTF8StringEncoding error:nil];
  if(!pushes)[fm setAttributes:@{NSFileModificationDate:[NSDate dateWithTimeIntervalSince1970:1]} ofItemAtPath:progress error:nil];
  setenv("TOTK_CLOUD_SAVE_DIR",root.fileSystemRepresentation,1);
  runtime_cloud_save_start(local.fileSystemRepresentation,profile.fileSystemRepresentation);
  if(pushes || [mode isEqualToString:@"mismatch"])push();
  fflush(NULL);
 }
 return 0;
}
