param([int]$Mute)

Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Diagnostics;
using System.Collections.Generic;

namespace SpotifyCtrl {

[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    int f1(); // EnumAudioEndpoints
    [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppDevice);
}

[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    [PreserveSig] int Activate(ref Guid iid, uint dwClsCtx, IntPtr pActivationParams,
        [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
}

[ComImport, Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionManager2 {
    int f1(); // GetAudioSessionControl (IAudioSessionManager)
    int f2(); // GetSimpleAudioVolume  (IAudioSessionManager)
    [PreserveSig] int GetSessionEnumerator(out IAudioSessionEnumerator SessionEnum);
}

[ComImport, Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionEnumerator {
    [PreserveSig] int GetCount(out int SessionCount);
    [PreserveSig] int GetSession(int SessionCount, out IAudioSessionControl2 Session);
}

[ComImport, Guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionControl2 {
    int f1(); int f2(); int f3(); int f4(); int f5();
    int f6(); int f7(); int f8(); int f9(); int f10(); int f11();
    [PreserveSig] int GetProcessId(out uint pRetVal);
}

[ComImport, Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ISimpleAudioVolume {
    int f1(); // SetMasterVolume
    int f2(); // GetMasterVolume
    [PreserveSig] int SetMute(bool bMute, ref Guid EventContext);
}

public static class Muter {
    static readonly Guid CLSID = new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E");
    static readonly Guid IID_Manager2 = new Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F");

    public static void SetMute(bool mute) {
        var type = Type.GetTypeFromCLSID(CLSID);
        var enumerator = (IMMDeviceEnumerator)Activator.CreateInstance(type);
        IMMDevice device;
        enumerator.GetDefaultAudioEndpoint(0, 1, out device); // eRender=0, eMultimedia=1
        var iid = IID_Manager2;
        object managerObj;
        device.Activate(ref iid, 23, IntPtr.Zero, out managerObj);
        var manager = (IAudioSessionManager2)managerObj;
        IAudioSessionEnumerator sessionEnum;
        manager.GetSessionEnumerator(out sessionEnum);
        int count;
        sessionEnum.GetCount(out count);

        var pids = new HashSet<uint>();
        foreach (var p in Process.GetProcessesByName("Spotify"))
            pids.Add((uint)p.Id);

        for (int i = 0; i < count; i++) {
            try {
                IAudioSessionControl2 session;
                sessionEnum.GetSession(i, out session);
                uint pid;
                session.GetProcessId(out pid);
                if (!pids.Contains(pid)) continue;
                var vol = session as ISimpleAudioVolume;
                if (vol == null) continue;
                var empty = Guid.Empty;
                vol.SetMute(mute, ref empty);
            } catch { }
        }
    }
}
}
'@ -ErrorAction SilentlyContinue

try { [SpotifyCtrl.Muter]::SetMute($Mute -ne 0) } catch { }
