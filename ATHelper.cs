using System; using System.Drawing; using System.Runtime.InteropServices;
public class AT {
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, int f);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, int x, int y, uint d, int e);
    [DllImport("user32.dll")] static extern short VkKeyScan(char c);
    [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, int extra);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
    public static void Shot(long h, string f) {
        var ih = new IntPtr(h); ShowWindow(ih,9); System.Threading.Thread.Sleep(300);
        RECT r; GetWindowRect(ih,out r); int w=r.R-r.L, hh=r.B-r.T;
        var b=new Bitmap(w,hh); using(var g=Graphics.FromImage(b)){PrintWindow(ih,g.GetHdc(),2);g.ReleaseHdc();}
        b.Save(f); b.Dispose();
    }
    public static void Click(long hwnd, int x, int y) {
        SetForegroundWindow(new IntPtr(hwnd)); System.Threading.Thread.Sleep(300);
        SetCursorPos(x,y); System.Threading.Thread.Sleep(150);
        mouse_event(0x0002,x,y,0,0); System.Threading.Thread.Sleep(80); mouse_event(0x0004,x,y,0,0);
        System.Threading.Thread.Sleep(200);
    }
    public static void TypeStr(string text) {
        foreach(char c in text) {
            short vk = VkKeyScan(c);
            byte key = (byte)(vk & 0xFF); bool shift = (vk & 0x100) != 0;
            if(shift) keybd_event(0x10,0,0,0);
            keybd_event(key,0,0,0); keybd_event(key,0,2,0);
            if(shift) keybd_event(0x10,0,2,0);
            System.Threading.Thread.Sleep(40);
        }
    }
    public static void KeyPress(byte vk) { keybd_event(vk,0,0,0); System.Threading.Thread.Sleep(50); keybd_event(vk,0,2,0); }
}
