"""Pinned, read-only witness-source guard for a single owned recovery run.

Initial construction fully validates the confirmed checkpoint, marker and
registry/witness relationships. Later checks hash the exact compressed bytes
and fence pointer, manifest and file identities without re-parsing XML.
The optional metadata-only check is for a bounded caller-owned phase between
full checks. It does not establish unchanged save bytes or exclude writers.
The caller must still fence writers; no check can prevent a later mutation.
"""
import hashlib
import json
import os
import stat
from pathlib import Path

from authority.checkpoints import CheckpointStore
from authority.marker_evidence import verify_saved_marker
import authority_recovery_registry_status as registry
import authority_registry_witness_save_evidence as witness


def _identity(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _same_path(fd, directory, name):
    current=os.fstat(fd)
    by_name=os.stat(name,dir_fd=directory,follow_symlinks=False)
    if _identity(current)!=_identity(by_name): raise ValueError('source file path changed')
    return current


def _open_dir(name, *, parent=None):
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
    fd=os.open(name,flags,dir_fd=parent)
    try:
        before=os.fstat(fd)
        named=os.stat(name,dir_fd=parent,follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode) or _identity(before)!=_identity(named):
            raise ValueError('source directory changed')
        return fd
    except BaseException:
        os.close(fd);raise


def _read_file(parent, name, *, limit, expected=None, digest=False):
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
    try:
        before=_same_path(fd,parent,name)
        if not stat.S_ISREG(before.st_mode) or before.st_size>limit:
            raise ValueError('source file invalid')
        if expected is not None and _identity(before)!=expected:
            raise ValueError('source file identity changed')
        parts=[];sha=hashlib.sha256();size=0
        while True:
            chunk=os.read(fd,min(1024*1024,limit+1-size))
            if not chunk: break
            size+=len(chunk)
            if size>limit: raise ValueError('source file too large')
            if digest: sha.update(chunk)
            else: parts.append(chunk)
        after=_same_path(fd,parent,name)
        if _identity(before)!=_identity(after) or size!=after.st_size:
            raise ValueError('source changed while reading')
        return (_identity(after),sha.hexdigest() if digest else b''.join(parts),size)
    finally: os.close(fd)


def _stat_file(parent, name, *, limit):
    """Pin/check a regular file's identity without reading its contents."""
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
    try:
        before=_same_path(fd,parent,name)
        if not stat.S_ISREG(before.st_mode) or before.st_size>limit:
            raise ValueError('source file invalid')
        after=_same_path(fd,parent,name)
        if _identity(before)!=_identity(after):
            raise ValueError('source changed during metadata check')
        return (_identity(after),None,after.st_size)
    finally: os.close(fd)


class WitnessSourceGuard:
    """Not a writer lock; use only under the existing isolated-source fence."""
    def __init__(self,store,checkpoint):
        if not isinstance(store,CheckpointStore): raise ValueError('checkpoint store required')
        # Reopen the source as an independent reader. The publisher can reuse
        # its XML validation for downloads; this guard verifies the saved XML
        # itself before and after taking the file-identity snapshot.
        source_reader = CheckpointStore(store.directory)
        confirmed = source_reader.latest_marked_confirmed()
        if confirmed!=checkpoint or store.latest()!=checkpoint:
            raise ValueError('source not current and confirmed')
        status=registry.saved_status(checkpoint)
        if len(status)!=4 or status[0]!='ready': raise ValueError('witness source not ready')
        witness.verify(checkpoint,status[1],status[2])
        verify_saved_marker(checkpoint,checkpoint.marker,script_name='X4AuthorityCheckpointMarker',cue_name='MarkerState')
        self.store=store;self.checkpoint=checkpoint;self.status=status
        self._baseline=self._capture()
        current,confirmed,manifest=self._baseline['bytes']
        if current!=confirmed: raise ValueError('source pointer mismatch')
        try: pointer=json.loads(current.decode('ascii')); metadata=json.loads(manifest.decode('ascii'))
        except (ValueError,UnicodeError) as error: raise ValueError('invalid source metadata') from error
        if (type(pointer) is not dict or set(pointer)!={'version','id','manifest_sha256'} or
            pointer['version']!=1 or pointer['id']!=checkpoint.checkpoint_id or
            pointer['manifest_sha256']!=hashlib.sha256(manifest).hexdigest() or
            type(metadata) is not dict or metadata.get('id')!=checkpoint.checkpoint_id or
            metadata.get('sha256')!=checkpoint.sha256 or metadata.get('size')!=checkpoint.size or
            metadata.get('marker')!=checkpoint.marker):
            raise ValueError('source metadata differs from validated checkpoint')
        # Revalidate after snapshot so it cannot bind a different source than
        # the one validated before it. Any later writer is caller-fenced.
        if source_reader.latest_marked_confirmed() != checkpoint or store.latest() != checkpoint:
            raise ValueError('source changed during initialization')
        self.check()

    def _capture(self, *, metadata_only=False):
        root=_open_dir(str(self.store.directory))
        try:
            directory=_open_dir(self.checkpoint.checkpoint_id,parent=root)
            try:
                root_id=_identity(os.fstat(root));directory_id=_identity(os.fstat(directory))
                cur=_read_file(root,'CURRENT',limit=4096)
                conf=_read_file(root,'CONFIRMED',limit=4096)
                manifest=_read_file(directory,'manifest.json',limit=4096)
                if metadata_only:
                    save=_stat_file(directory,'save.xml.gz',limit=self.checkpoint.size)
                else:
                    save=_read_file(directory,'save.xml.gz',limit=self.checkpoint.size,digest=True)
                # Re-read metadata after checking the save, including name identities.
                if (cur!=_read_file(root,'CURRENT',limit=4096) or
                    conf!=_read_file(root,'CONFIRMED',limit=4096) or
                    manifest!=_read_file(directory,'manifest.json',limit=4096) or
                    _identity(os.stat('save.xml.gz',dir_fd=directory,follow_symlinks=False))!=save[0]):
                    raise ValueError('source metadata changed during check')
                if (_identity(os.fstat(root))!=root_id or _identity(os.fstat(directory))!=directory_id or
                    _identity(os.stat(str(self.store.directory),follow_symlinks=False))!=root_id or
                    _identity(os.stat(self.checkpoint.checkpoint_id,dir_fd=root,follow_symlinks=False))!=directory_id):
                    raise ValueError('source directory changed during check')
                if (save[2]!=self.checkpoint.size or
                    (not metadata_only and save[1]!=self.checkpoint.sha256)):
                    raise ValueError('source compressed bytes changed')
                return {'directories':(root_id,directory_id),
                        'files':(cur[0],conf[0],manifest[0],save[0]),'bytes':(cur[1],conf[1],manifest[1])}
            finally: os.close(directory)
        finally: os.close(root)

    def check(self):
        if self._capture()!=self._baseline: raise ValueError('witness source changed')
        return True

    def check_metadata(self):
        """Check pinned identities and exact small metadata, without save reads.

        Only for a bounded caller-owned phase between full check() boundaries.
        This detects ordinary metadata/identity changes; it is neither proof of
        unchanged compressed bytes nor a writer lock. The caller fences writers
        and owns the decision to enter/leave that phase.
        """
        if self._capture(metadata_only=True)!=self._baseline:
            raise ValueError('witness source changed')
        return True
