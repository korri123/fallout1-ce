#ifndef FALLOUT_INT_AUDIOMP3_H_
#define FALLOUT_INT_AUDIOMP3_H_

namespace fallout {

int audiomp3Open(const char* fname, int flags);
int audiomp3CloseFile(int fileHandle);
int audiomp3Read(int fileHandle, void* buffer, unsigned int size);
long audiomp3Seek(int fileHandle, long offset, int origin);
long audiomp3FileSize(int fileHandle);
long audiomp3Tell(int fileHandle);
int audiomp3Write(int handle, const void* buf, unsigned int size);
void audiomp3Close();

} // namespace fallout

#endif /* FALLOUT_INT_AUDIOMP3_H_ */
