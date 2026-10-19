"""OpenGL board for the Windows Chess port.

Geometry and camera come from MBCBoardView in the Chess 3.18 IDA database.
Revolution pieces are the radius/height tables passed to sub_100004A40
(scaled by 0.85, 32 slices). The knight's horse and the queen's cross are
the immediate-mode vertices from those display lists. Square textures are
the style PNGs shipped in the app bundle.
"""

from __future__ import annotations

import ctypes
import json
import math
import time
from ctypes import wintypes
from pathlib import Path

import tkinter as tk
from PIL import Image, ImageDraw, ImageFont

from chess_rules import BLACK, PAWN, WHITE, color_of, kind_of

# Styles and selected_piece_texture.png from Apple's Chess.app Resources.
ASSETS = Path(__file__).with_name("Resources")
MESH_PATH = Path(__file__).with_name("piece_meshes.json")

SCALE = 0.85
SLICES = 32

# radius, height pairs including the terminating 0, 0. Values are pre-scale.
# Keys are the original's display list numbers: 1 king, 2 queen, 3 bishop,
# 4 knight, 5 rook, 6 pawn. MESH_LIST maps the rules' codes onto them.
PROFILES = {
    2: (  # queen
        [4.8, 4.8, 3.4, 3.4, 1.8, 1.4, 2.9, 1.8, 1.8, 2.0, 2.7, 2.4, 1.7, 0.95, 0.7, 0.9, 0.7, 0.0, 0.0],
        [0.0, 2.2, 4.0, 5.0, 8.0, 11.8, 11.8, 13.6, 15.2, 17.8, 19.2, 20.0, 20.0, 20.8, 20.8, 21.4, 22.0, 22.0, 0.0],
    ),
    1: (  # king; the cross is queen_extra in piece_meshes.json
        [5.0, 5.0, 3.5, 3.5, 2.0, 1.4, 3.0, 2.0, 2.0, 2.8, 1.6, 1.6, 0.0, 0.0],
        [0.0, 2.0, 3.0, 4.6, 7.6, 12.6, 12.6, 14.6, 15.6, 19.1, 19.7, 20.1, 20.1, 0.0],
    ),
    3: (  # bishop
        [4.0, 4.0, 2.5, 2.5, 1.5, 1.2, 2.5, 1.7, 1.7, 2.2, 2.2, 1.0, 0.8, 1.2, 0.8, 0.0, 0.0],
        [0.0, 2.0, 3.0, 4.0, 7.0, 9.4, 9.4, 11.0, 12.2, 13.2, 14.8, 16.0, 17.0, 17.7, 18.4, 18.4, 0.0],
    ),
    4: (  # knight pedestal; the horse mesh sits on y = 5.8 * 0.85
        [4.1, 4.1, 2.0, 2.0, 2.6, 0.0],
        [0.0, 2.0, 3.6, 4.8, 5.8, 0.0],
    ),
    5: (  # rook
        [3.8, 3.8, 2.6, 2.0, 2.8, 2.8, 2.2, 2.2, 0.0, 0.0],
        [0.0, 2.0, 5.0, 10.2, 10.2, 13.6, 13.6, 13.0, 13.0, 0.0],
    ),
    PAWN: (
        [3.5, 3.5, 2.5, 2.5, 1.5, 1.0, 1.8, 1.0, 2.0, 1.0, 0.0, 0.0],
        [0.0, 2.0, 3.0, 4.0, 6.0, 8.8, 8.8, 9.2, 11.6, 13.4, 13.4, 0.0],
    ),
}

GL_DEPTH_BUFFER_BIT = 0x00000100
GL_STENCIL_BUFFER_BIT = 0x00000400
GL_COLOR_BUFFER_BIT = 0x00004000
GL_QUADS = 0x0007
GL_TRIANGLES = 0x0004
GL_TRIANGLE_FAN = 0x0006
GL_TRIANGLE_STRIP = 0x0005
GL_LIGHTING = 0x0B50
GL_TEXTURE_2D = 0x0DE1
GL_BLEND = 0x0BE2
GL_DEPTH_TEST = 0x0B71
GL_STENCIL_TEST = 0x0B90
GL_NORMALIZE = 0x0BA1
GL_CULL_FACE = 0x0B44
GL_LIGHT0 = 0x4000
GL_AMBIENT = 0x1200
GL_DIFFUSE = 0x1201
GL_SPECULAR = 0x1202
GL_EMISSION = 0x1600
GL_AMBIENT_AND_DIFFUSE = 0x1602
GL_LIGHT_MODEL_AMBIENT = 0x0B53
GL_POSITION = 0x1203
GL_FRONT_AND_BACK = 0x0408
GL_SHININESS = 0x1601
GL_MODULATE = 0x2100
GL_TEXTURE_ENV = 0x2300
GL_TEXTURE_ENV_MODE = 0x2200
GL_REPEAT = 0x2901
GL_LINEAR = 0x2601
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_RGBA = 0x1908
GL_UNSIGNED_BYTE = 0x1401
GL_MODELVIEW = 0x1700
GL_PROJECTION = 0x1701
GL_SMOOTH = 0x1D01
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_ALWAYS = 0x0207
GL_EQUAL = 0x0202
GL_KEEP = 0x1E00
GL_REPLACE = 0x1E01
GL_LIGHT_MODEL_LOCAL_VIEWER = 0x0B51
GL_LIGHT_MODEL_COLOR_CONTROL = 0x81F8
GL_SEPARATE_SPECULAR_COLOR = 0x81FA
GL_COMPILE = 0x1300
GL_LINEAR_MIPMAP_LINEAR = 0x2703
GL_MULTISAMPLE = 0x809D
GL_NICEST = 0x1102
GL_PERSPECTIVE_CORRECTION_HINT = 0x0C50
GL_EXTENSIONS = 0x1F03
GL_TEXTURE_MAX_ANISOTROPY = 0x84FE
GL_MAX_TEXTURE_MAX_ANISOTROPY = 0x84FF
WGL_DRAW_TO_WINDOW = 0x2001
WGL_SUPPORT_OPENGL = 0x2010
WGL_DOUBLE_BUFFER = 0x2011
WGL_PIXEL_TYPE = 0x2013
WGL_TYPE_RGBA = 0x202B
WGL_COLOR_BITS = 0x2014
WGL_DEPTH_BITS = 0x2022
WGL_STENCIL_BITS = 0x2023
WGL_SAMPLE_BUFFERS = 0x2041
WGL_SAMPLES = 0x2042
GL_LIGHT1 = 0x4001
GL_SPOT_DIRECTION = 0x1204
GL_SPOT_EXPONENT = 0x1205
GL_SPOT_CUTOFF = 0x1206
GL_CONSTANT_ATTENUATION = 0x1207
GL_QUADRATIC_ATTENUATION = 0x1209
GL_ALPHA = 0x1906
GL_UNPACK_ALIGNMENT = 0x0CF5

# Rules piece code to the original's display list (generateModelLists).
MESH_LIST = {1: 2, 2: 1}
# Piece order of dword_1000572E0, used for pieces in hand.
HAND_ORDER = (PAWN, 3, 4, 5, 1)  # pawn, bishop, knight, rook, queen
# Regions from -[MBCBoardView positionToSquareOrRegion:].
REGION_PROMO_WHITE = 113
REGION_PROMO_BLACK = 114
REGION_FRAME = 115
HAND_BASE = 128


def bsd_random(seed: int, count: int) -> list[int]:
    """srandom(seed) then random() as in the macOS libc (FreeBSD TYPE_3).

    -[MBCBoardView drawBoard:] calls srandom(1) and draws one random()
    per square to offset its texture.
    """
    state = [0] * 31
    state[0] = seed
    for i in range(1, 31):
        hi, lo = divmod(state[i - 1], 127773)
        word = 16807 * lo - 2836 * hi
        if word < 0:
            word += 0x7FFFFFFF
        state[i] = word
    front, rear = 3, 0
    out = []
    for step in range(310 + count):
        state[front] = (state[front] + state[rear]) & 0xFFFFFFFF
        value = (state[front] >> 1) & 0x7FFFFFFF
        front = (front + 1) % 31
        rear = (rear + 1) % 31
        if step >= 310:
            out.append(value)
    return out


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", ctypes.c_void_p),
        ("fErase", ctypes.c_int),
        ("rcPaint", RECT),
        ("fRestore", ctypes.c_int),
        ("fIncUpdate", ctypes.c_int),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)


def enable_dpi() -> None:
    """Draw at the monitor's real pixel size instead of a stretched bitmap."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class PIXELFORMATDESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("nSize", ctypes.c_ushort),
        ("nVersion", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("iPixelType", ctypes.c_ubyte),
        ("cColorBits", ctypes.c_ubyte),
        ("cRedBits", ctypes.c_ubyte),
        ("cRedShift", ctypes.c_ubyte),
        ("cGreenBits", ctypes.c_ubyte),
        ("cGreenShift", ctypes.c_ubyte),
        ("cBlueBits", ctypes.c_ubyte),
        ("cBlueShift", ctypes.c_ubyte),
        ("cAlphaBits", ctypes.c_ubyte),
        ("cAlphaShift", ctypes.c_ubyte),
        ("cAccumBits", ctypes.c_ubyte),
        ("cAccumRedBits", ctypes.c_ubyte),
        ("cAccumGreenBits", ctypes.c_ubyte),
        ("cAccumBlueBits", ctypes.c_ubyte),
        ("cAccumAlphaBits", ctypes.c_ubyte),
        ("cDepthBits", ctypes.c_ubyte),
        ("cStencilBits", ctypes.c_ubyte),
        ("cAuxBuffers", ctypes.c_ubyte),
        ("iLayerType", ctypes.c_ubyte),
        ("bReserved", ctypes.c_ubyte),
        ("dwLayerMask", ctypes.c_uint),
        ("dwVisibleMask", ctypes.c_uint),
        ("dwDamageMask", ctypes.c_uint),
    ]


def _bind(dll, name, restype, *argtypes):
    fn = getattr(dll, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


class GL:
    def __init__(self) -> None:
        self.gl = ctypes.WinDLL("opengl32")
        self.glu = ctypes.WinDLL("glu32")
        self.gdi = ctypes.WinDLL("gdi32")
        self.user = ctypes.WinDLL("user32")
        f = ctypes.c_float
        i = ctypes.c_int
        u = ctypes.c_uint
        d = ctypes.c_double
        p = ctypes.c_void_p
        self.GetDC = _bind(self.user, "GetDC", p, p)
        self.ReleaseDC = _bind(self.user, "ReleaseDC", i, p, p)
        self.ChoosePixelFormat = _bind(self.gdi, "ChoosePixelFormat", i, p, p)
        self.SetPixelFormat = _bind(self.gdi, "SetPixelFormat", i, p, i, p)
        self.SwapBuffers = _bind(self.gdi, "SwapBuffers", i, p)
        self.wglCreateContext = _bind(self.gl, "wglCreateContext", p, p)
        self.wglMakeCurrent = _bind(self.gl, "wglMakeCurrent", i, p, p)
        self.wglDeleteContext = _bind(self.gl, "wglDeleteContext", i, p)
        self.glBegin = _bind(self.gl, "glBegin", None, u)
        self.glEnd = _bind(self.gl, "glEnd", None)
        self.glVertex3f = _bind(self.gl, "glVertex3f", None, f, f, f)
        self.glNormal3f = _bind(self.gl, "glNormal3f", None, f, f, f)
        self.glTexCoord2f = _bind(self.gl, "glTexCoord2f", None, f, f)
        self.glColor4f = _bind(self.gl, "glColor4f", None, f, f, f, f)
        self.glEnable = _bind(self.gl, "glEnable", None, u)
        self.glDisable = _bind(self.gl, "glDisable", None, u)
        self.glClear = _bind(self.gl, "glClear", None, u)
        self.glClearColor = _bind(self.gl, "glClearColor", None, f, f, f, f)
        self.glViewport = _bind(self.gl, "glViewport", None, i, i, i, i)
        self.glMatrixMode = _bind(self.gl, "glMatrixMode", None, u)
        self.glLoadIdentity = _bind(self.gl, "glLoadIdentity", None)
        self.glPushMatrix = _bind(self.gl, "glPushMatrix", None)
        self.glPopMatrix = _bind(self.gl, "glPopMatrix", None)
        self.glTranslatef = _bind(self.gl, "glTranslatef", None, f, f, f)
        self.glRotatef = _bind(self.gl, "glRotatef", None, f, f, f, f)
        self.glScalef = _bind(self.gl, "glScalef", None, f, f, f)
        self.glShadeModel = _bind(self.gl, "glShadeModel", None, u)
        self.glLightModeli = _bind(self.gl, "glLightModeli", None, u, i)
        self.glLightfv = _bind(self.gl, "glLightfv", None, u, u, p)
        self.glLightModelfv = _bind(self.gl, "glLightModelfv", None, u, p)
        self.glMaterialfv = _bind(self.gl, "glMaterialfv", None, u, u, p)
        self.glMaterialf = _bind(self.gl, "glMaterialf", None, u, u, f)
        self.glTexEnvi = _bind(self.gl, "glTexEnvi", None, u, u, i)
        self.glGenTextures = _bind(self.gl, "glGenTextures", None, i, p)
        self.glBindTexture = _bind(self.gl, "glBindTexture", None, u, u)
        self.glTexParameteri = _bind(self.gl, "glTexParameteri", None, u, u, i)
        self.glTexImage2D = _bind(self.gl, "glTexImage2D", None, u, i, i, i, i, i, u, u, p)
        self.glBlendFunc = _bind(self.gl, "glBlendFunc", None, u, u)
        self.glDepthMask = _bind(self.gl, "glDepthMask", None, u)
        self.glColorMask = _bind(self.gl, "glColorMask", None, u, u, u, u)
        self.glStencilFunc = _bind(self.gl, "glStencilFunc", None, u, i, u)
        self.glStencilOp = _bind(self.gl, "glStencilOp", None, u, u, u)
        self.glStencilMask = _bind(self.gl, "glStencilMask", None, u)
        self.glCullFace = _bind(self.gl, "glCullFace", None, u)
        self.glReadPixels = _bind(self.gl, "glReadPixels", None, i, i, i, i, u, u, p)
        self.glGenLists = _bind(self.gl, "glGenLists", u, i)
        self.glNewList = _bind(self.gl, "glNewList", None, u, u)
        self.glEndList = _bind(self.gl, "glEndList", None)
        self.glCallList = _bind(self.gl, "glCallList", None, u)
        self.glHint = _bind(self.gl, "glHint", None, u, u)
        self.glTexParameterf = _bind(self.gl, "glTexParameterf", None, u, u, f)
        self.glGetIntegerv = _bind(self.gl, "glGetIntegerv", None, u, p)
        self.glGetFloatv = _bind(self.gl, "glGetFloatv", None, u, p)
        self.glGetString = _bind(self.gl, "glGetString", ctypes.c_char_p, u)
        self.gluPerspective = _bind(self.glu, "gluPerspective", None, d, d, d, d)
        self.gluLookAt = _bind(self.glu, "gluLookAt", None, d, d, d, d, d, d, d, d, d)
        self.gluBuild2DMipmaps = _bind(self.glu, "gluBuild2DMipmaps", i, u, i, i, i, u, u, p)
        self.wglGetProcAddress = _bind(self.gl, "wglGetProcAddress", p, ctypes.c_char_p)
        self.CreateWindowExW = _bind(self.user, "CreateWindowExW", p, u, ctypes.c_wchar_p, ctypes.c_wchar_p, u, i, i, i, i, p, p, p, p)
        self.DestroyWindow = _bind(self.user, "DestroyWindow", i, p)
        self.MoveWindow = _bind(self.user, "MoveWindow", i, p, i, i, i, i, i)
        self.GetClientRect = _bind(self.user, "GetClientRect", i, p, p)
        self.BeginPaint = _bind(self.user, "BeginPaint", p, p, p)
        self.EndPaint = _bind(self.user, "EndPaint", i, p, p)
        self.DefWindowProcW = _bind(self.user, "DefWindowProcW", ctypes.c_ssize_t, p, u, ctypes.c_size_t, ctypes.c_ssize_t)
        self.CallWindowProcW = _bind(self.user, "CallWindowProcW", ctypes.c_ssize_t, p, p, u, ctypes.c_size_t, ctypes.c_ssize_t)
        self.SetWindowLongPtrW = _bind(self.user, "SetWindowLongPtrW", p, p, i, p)
        self.glOrtho = _bind(self.gl, "glOrtho", None, d, d, d, d, d, d)
        self.glColor3f = _bind(self.gl, "glColor3f", None, f, f, f)
        self.glVertex2f = _bind(self.gl, "glVertex2f", None, f, f)
        self.glLightf = _bind(self.gl, "glLightf", None, u, u, f)
        self.glLighti = _bind(self.gl, "glLighti", None, u, u, i)
        self.glPixelStorei = _bind(self.gl, "glPixelStorei", None, u, i)
        self.SetCapture = _bind(self.user, "SetCapture", p, p)
        self.ReleaseCapture = _bind(self.user, "ReleaseCapture", i)
        self.ShowCursor = _bind(self.user, "ShowCursor", i, i)
        self.LoadCursorW = _bind(self.user, "LoadCursorW", p, p, p)
        self.SetCursor = _bind(self.user, "SetCursor", p, p)
        self.hdc = None
        self.rc = None
        self.hwnd = None
        self.samples = 0
        self._choose_arb = None

    def _legacy_format(self, hdc) -> None:
        pfd = PIXELFORMATDESCRIPTOR()
        pfd.nSize = ctypes.sizeof(pfd)
        pfd.nVersion = 1
        pfd.dwFlags = 0x25
        pfd.iPixelType = 0
        pfd.cColorBits = 32
        pfd.cDepthBits = 24
        pfd.cStencilBits = 8
        pfd.cAlphaBits = 8
        fmt = self.ChoosePixelFormat(hdc, ctypes.byref(pfd))
        if not fmt or not self.SetPixelFormat(hdc, fmt, ctypes.byref(pfd)):
            raise RuntimeError("OpenGL pixel format was rejected")

    def _load_multisample_chooser(self) -> None:
        dummy = self.CreateWindowExW(0, "Static", "", 0, 0, 0, 8, 8, None, None, None, None)
        hdc = self.GetDC(dummy)
        self._legacy_format(hdc)
        rc = self.wglCreateContext(hdc)
        self.wglMakeCurrent(hdc, rc)
        addr = self.wglGetProcAddress(b"wglChoosePixelFormatARB")
        if addr:
            prototype = ctypes.WINFUNCTYPE(
                ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.c_void_p,
                ctypes.c_uint, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint),
            )
            self._choose_arb = prototype(addr)
        self.wglMakeCurrent(None, None)
        self.wglDeleteContext(rc)
        self.ReleaseDC(dummy, hdc)
        self.DestroyWindow(dummy)

    def _choose_multisample(self, hdc, samples: int) -> int:
        if not self._choose_arb:
            return 0
        attribs = (ctypes.c_int * 19)(
            WGL_DRAW_TO_WINDOW, 1,
            WGL_SUPPORT_OPENGL, 1,
            WGL_DOUBLE_BUFFER, 1,
            WGL_PIXEL_TYPE, WGL_TYPE_RGBA,
            WGL_COLOR_BITS, 32,
            WGL_DEPTH_BITS, 24,
            WGL_STENCIL_BITS, 8,
            WGL_SAMPLE_BUFFERS, 1,
            WGL_SAMPLES, samples,
            0,
        )
        formats = (ctypes.c_int * 8)()
        count = ctypes.c_uint()
        ok = self._choose_arb(hdc, attribs, None, 8, formats, ctypes.byref(count))
        if ok and count.value:
            return formats[0]
        return 0

    def attach(self, parent_hwnd: int, width: int, height: int, wndproc) -> None:
        self._load_multisample_chooser()
        style = 0x40000000 | 0x10000000 | 0x04000000  # child, visible, clip siblings
        self.hwnd = self.CreateWindowExW(0, "Static", "", style, 0, 0, width, height, parent_hwnd, None, None, None)
        if not self.hwnd:
            raise RuntimeError("OpenGL window was not created")
        self._old_proc = self.SetWindowLongPtrW(self.hwnd, -4, ctypes.cast(wndproc, ctypes.c_void_p))
        self.hdc = self.GetDC(self.hwnd)
        fmt = 0
        for samples in (8, 4, 2):
            fmt = self._choose_multisample(self.hdc, samples)
            if fmt:
                self.samples = samples
                break
        if fmt:
            described = PIXELFORMATDESCRIPTOR()
            described.nSize = ctypes.sizeof(described)
            if not self.SetPixelFormat(self.hdc, fmt, ctypes.byref(described)):
                fmt = 0
        if not fmt:
            self._legacy_format(self.hdc)
            self.samples = 0
        self.rc = self.wglCreateContext(self.hdc)
        if not self.rc or not self.wglMakeCurrent(self.hdc, self.rc):
            raise RuntimeError("OpenGL context was not created")
        interval = self.wglGetProcAddress(b"wglSwapIntervalEXT")
        if interval:
            ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int)(interval)(1)

    def client_size(self) -> tuple[int, int]:
        rect = RECT()
        if self.hwnd and self.GetClientRect(self.hwnd, ctypes.byref(rect)):
            return max(rect.right - rect.left, 1), max(rect.bottom - rect.top, 1)
        return 1, 1

    def place(self, width: int, height: int) -> None:
        if self.hwnd:
            self.MoveWindow(self.hwnd, 0, 0, max(width, 1), max(height, 1), True)

    def make_current(self) -> None:
        self.wglMakeCurrent(self.hdc, self.rc)

    def swap(self) -> None:
        self.SwapBuffers(self.hdc)


def _norm(x: float, y: float, z: float) -> tuple[float, float, float]:
    length = math.sqrt(x * x + y * y + z * z) or 1.0
    return x / length, y / length, z / length


def _lathe(radii: list[float], heights: list[float]) -> list[tuple]:
    """Return triangle batches in the piece's final Y-up space."""
    tris: list[tuple] = []
    index = 0
    while index + 1 < len(radii):
        r0 = radii[index] * SCALE
        h0 = heights[index] * SCALE
        r1 = radii[index + 1] * SCALE
        h1 = heights[index + 1] * SCALE
        if radii[index + 1] == 0.0 and heights[index + 1] == 0.0:
            break
        dh = h1 - h0
        if abs(dh) >= 1e-5:
            t_mul = -2.0 * abs(dh) / 8.5
            rising = dh >= 0.0
            base_r, top_r = (r0, r1) if rising else (r1, r0)
            # GLU inside orientation flips the normal when the segment goes down.
            flip = 1.0 if rising else -1.0
            for step in range(SLICES):
                a0 = math.tau * step / SLICES
                a1 = math.tau * (step + 1) / SLICES
                quad = []
                for ang, radius, v_at, along in (
                    (a0, base_r, 0.0, 0.0),
                    (a1, base_r, 0.0, 0.0),
                    (a1, top_r, 1.0, 1.0),
                    (a0, top_r, 1.0, 1.0),
                ):
                    c, s = math.cos(ang), math.sin(ang)
                    run = top_r - base_r
                    nx, ny, nz = _norm(dh * c * flip, -run * flip, -dh * s * flip)
                    height = (h0 if rising else h1) + along * abs(dh)
                    quad.append((radius * c, height, -radius * s, nx, ny, nz, (step + (ang != a0)) / SLICES * 2.0, v_at * t_mul))
                tris.append(("fan", quad))
        else:
            inner, outer = (r1, r0) if r1 <= r0 else (r0, r1)
            upward = r1 <= r0
            ny = 1.0 if upward else -1.0
            ring = []
            for step in range(SLICES + 1):
                ang = math.tau * step / SLICES
                c, s = math.cos(ang), math.sin(ang)
                for radius, tex in ((inner, inner / outer if outer else 0.0), (outer, 1.0)):
                    ring.append((radius * c, h0, -radius * s, 0.0, ny, 0.0, 2.0 * tex * c, -2.0 * tex * s))
            tris.append(("strip", ring))
        index += 1
    return tris


def _from_json(batches: list[dict], uv_scale: tuple[float, float]) -> list[tuple]:
    out = []
    su, sv = uv_scale
    for batch in batches:
        mode = "quad" if batch["mode"] == GL_QUADS else "tri"
        group: list[tuple] = []
        width = 4 if mode == "quad" else 3
        for vert in batch["verts"]:
            x, y, z = vert["v"]
            nx, ny, nz = vert.get("n", (0.0, 1.0, 0.0))
            u, v = vert.get("t", (0.25, 0.25))
            group.append((x, y, z, nx, ny, nz, u * su, v * sv))
            if len(group) == width:
                out.append((mode, group))
                group = []
        if group:
            out.append((mode, group))
    return out


def build_meshes() -> dict[int, list[tuple]]:
    raw = json.loads(MESH_PATH.read_text(encoding="utf-8"))
    meshes = {kind: _lathe(radii, heights) for kind, (radii, heights) in PROFILES.items()}
    meshes[1].extend(_from_json(raw["queen_extra"], (2.0, -2.0)))
    meshes[4].extend(_from_json(raw["knight_extra"], (2.0, -2.0)))
    return meshes


def square_center(sq: int) -> tuple[float, float, float]:
    return ((sq & 7) * 10.0 - 35.0, 0.0, 35.0 - (sq >> 3) * 10.0)


def hand_center(code: int) -> tuple[float, float, float]:
    """Where drawPiecesInHand puts a piece in hand (side | kind)."""
    sign = 1.0 if color_of(code) == WHITE else -1.0
    return (51.0, 0.0, sign * (7.0 + 8.0 * HAND_ORDER.index(kind_of(code))))


class Scene:
    def __init__(self) -> None:
        self.pos = None
        self.selected = None
        self.targets: set[int] = set()
        self.last: tuple | None = None
        self.hints: set[int] = set()
        self.board_style = "Wood"
        self.piece_style = "Wood"
        self.elevation = 60.0
        self.azimuth = 180.0
        self.hand_sel = None
        self.picked = None          # fPickedSquare, lit by the spotlight
        self.hint_move = None       # fHintMove, red arrow
        self.last_move = None       # fLastMove, blue arrow (Show Last Move)
        self.drag = None            # (square or hand code, x, z) for fSelectedPos
        self.promo_piece = None     # (side, kind) from drawPromotionPiece
        self.anim = None            # fSelectedDest while a move animates


class GLBoard(tk.Frame):
    def __init__(self, master, on_square, on_orbit) -> None:
        super().__init__(master, width=980, height=860, bg="#404040", highlightthickness=0)
        self.on_square = on_square
        # Called with (azimuth, elevation, done) while the frame is dragged.
        self.on_orbit = on_orbit
        self.scene = Scene()
        self.gl: GL | None = None
        self.meshes: dict | None = None
        self.piece_lists: dict[int, int] = {}
        self.textures: dict[tuple[str, str], int] = {}
        self.materials: dict[str, dict] = {}
        self.labels: dict[str, int] = {}
        self._drag = None
        self._click_x = (ctypes.c_int * 32)()
        self._click_y = (ctypes.c_int * 32)()
        self._click_n = ctypes.c_int(0)
        self._orbit_dx = ctypes.c_int(0)
        self._orbit_dy = ctypes.c_int(0)
        self._drag_x = ctypes.c_int(0)
        self._drag_y = ctypes.c_int(0)
        self._right_down = ctypes.c_int(0)
        self._left_down = ctypes.c_int(0)
        self._press_x = ctypes.c_int(0)
        self._press_y = ctypes.c_int(0)
        self._cur_x = ctypes.c_int(0)
        self._cur_y = ctypes.c_int(0)
        self._left_drag = ctypes.c_int(0)
        self._drop_n = ctypes.c_int(0)
        self._drop = (ctypes.c_int * 4)()
        self._paint_due = ctypes.c_int(0)
        self._press_n = ctypes.c_int(0)
        self._busy = ctypes.c_int(0)
        self._seen_press = 0
        self._manip = None
        self.on_click_at = None
        self.on_drop = None
        self.on_dragging = None
        self._shown_drag = False
        self._in_paint = False
        self._ready = False
        self.view_w = 1
        self.view_h = 1
        self._offsets = self._square_offsets()
        self._wndproc = WNDPROC(self._on_message)
        self.bind("<Map>", lambda _e: self.after(10, self._ensure))
        self.bind("<Configure>", self._on_configure)
        self.after(16, self._poll_clicks)

    def _square_offsets(self) -> list[float]:
        # random() * 1.1641532e-10 for each square, after srandom(1).
        return [value * 1.1641532e-10 for value in bsd_random(1, 64)]

    def _poll_clicks(self) -> None:
        if self._press_n.value != self._seen_press:
            self._seen_press = self._press_n.value
            if self._left_down.value and self.region_at(self._press_x.value, self._press_y.value) == REGION_FRAME:
                self._start_manipulation()
        if self._manip is not None:
            if self._left_down.value:
                self._manipulate()
            else:
                self._end_manipulation()
            self._click_n.value = 0
            self._drop_n.value = 0
            self.after(16, self._poll_clicks)
            return
        if self._left_down.value and self._left_drag.value and self.on_dragging is not None:
            self._shown_drag = True
            self.on_dragging(self._press_x.value, self._press_y.value, self._cur_x.value, self._cur_y.value)
        elif self._shown_drag and not self._left_down.value and self.on_dragging is not None:
            self._shown_drag = False
            self.on_dragging(0, 0, 0, 0)
        count = self._click_n.value
        self._click_n.value = 0
        for index in range(count):
            x = self._click_x[index]
            y = self._click_y[index]
            sq = self.pick(x, y)
            if self.on_click_at is not None:
                self.on_click_at(x, y, sq)
            elif sq is not None:
                self.on_square(sq)
        if self._drop_n.value and self.on_drop is not None:
            self._drop_n.value = 0
            self.on_drop(self._drop[0], self._drop[1], self._drop[2], self._drop[3])
        if self._paint_due.value:
            self._paint_due.value = 0
            self.redraw()
        self.after(16, self._poll_clicks)

    def set_scene(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self.scene, key, value)
        self._ensure()
        self.redraw()

    def _on_configure(self, _event) -> None:
        if self.gl is not None:
            self.gl.place(self.winfo_width(), self.winfo_height())
        self.after_idle(self.redraw)

    def set_busy(self, busy: bool) -> None:
        if bool(self._busy.value) == busy:
            return
        self._busy.value = 1 if busy else 0
        if self.gl is not None and self._manip is None:
            self.gl.SetCursor(self.gl.LoadCursorW(None, ctypes.c_void_p(32514 if busy else 32512)))

    def region_at(self, px: int, py: int) -> int | None:
        """-[MBCBoardView positionToSquareOrRegion:] for a window point.

        Returns a square 0-63, a hand piece (128 + code), a promotion piece
        (113 white, 114 black), the frame (115), or None.
        """
        point = self.hit_point(px, py)
        if point is None:
            return None
        x, z = point
        promo = self.scene.promo_piece
        if promo is not None:
            if promo[0] == BLACK and abs(x - 50.0) < 8.0 and abs(z - 35.0) < 8.0:
                return REGION_PROMO_BLACK
            if promo[0] == WHITE and abs(x + 50.0) < 8.0 and abs(z + 35.0) < 8.0:
                return REGION_PROMO_WHITE
        if abs(x - 51.0) < 4.0:
            along = abs(z) - 3.0
            if 0.0 < along < 40.0:
                kind = HAND_ORDER[min(int(along * 0.125), 4)]
                return HAND_BASE + (kind | (WHITE if z >= 0.0 else BLACK))
            return None
        if abs(x) > 40.0 or abs(z) > 40.0:
            if abs(x) >= 46.35 or abs(z) >= 46.35:
                return None
            return REGION_FRAME
        file = min(int((x + 40.0) / 10.0), 7)
        rank = min(int((40.0 - z) / 10.0), 7)
        return rank * 8 + file

    def _start_manipulation(self) -> None:
        """mouseDown: on the frame starts fInBoardManipulation."""
        x, y = self._press_x.value, self._press_y.value
        self._manip = {"orig": (x, y), "raw": self.scene.azimuth, "t": time.perf_counter()}
        if self.gl is not None:
            self.gl.ShowCursor(0)
        self.redraw()

    def _manipulate(self) -> None:
        """-[MBCBoardView dragAndRedraw:forceRedraw:] for region 115.

        A horizontal drag spins at half a degree per second per pixel of
        offset, snapping to multiples of 90 within 5 degrees. A vertical drag
        changes the elevation between 10 and 80 degrees.
        """
        manip = self._manip
        now = time.perf_counter()
        dt = now - manip["t"]
        manip["t"] = now
        ox, oy = manip["orig"]
        dx = self._cur_x.value - ox
        dy = oy - self._cur_y.value  # Cocoa's y grows upward
        scene = self.scene
        if abs(dx) > 10 and abs(dx) > abs(dy):
            raw = math.fmod(dt * dx * 0.5 + manip["raw"] + 360.0, 360.0)
            manip["raw"] = raw
            rem = math.fmod(raw, 90.0)
            if rem < 5.0:
                azimuth = raw - rem
            elif rem > 85.0:
                azimuth = raw + (90.0 - rem)
            else:
                azimuth = raw
            self.on_orbit(azimuth, scene.elevation, False)
        elif abs(dy) > 10:
            elevation = max(min(dt * dy * -0.5 + scene.elevation, 80.0), 10.0)
            self.on_orbit(scene.azimuth, elevation, False)
        else:
            self.redraw()

    def _end_manipulation(self) -> None:
        self._manip = None
        if self.gl is not None:
            self.gl.ShowCursor(1)
        self.on_orbit(self.scene.azimuth, self.scene.elevation, True)

    def _point(self, lparam: int) -> tuple[int, int]:
        return ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short((lparam >> 16) & 0xFFFF).value

    def _on_message(self, hwnd, msg, wparam, lparam):
        # Stay tiny. Drawing or talking to Tk from this Windows callback
        # re-enters Python and crashes the process on the next move.
        if msg == 0x0084 or msg == 0x0014:
            return 1
        if msg == 0x0020 and self.gl is not None and (lparam & 0xFFFF) == 1:
            # WM_SETCURSOR over the client area: hourglass while the computer thinks.
            self.gl.SetCursor(self.gl.LoadCursorW(None, ctypes.c_void_p(32514 if self._busy.value else 32512)))
            return 1
        if msg == 0x000F and self.gl is not None:
            # Validate only, so the Static class does not paint over the board,
            # and let _poll_clicks redraw on the Tk side.
            ps = PAINTSTRUCT()
            self.gl.BeginPaint(hwnd, ctypes.byref(ps))
            self.gl.EndPaint(hwnd, ctypes.byref(ps))
            self._paint_due.value = 1
            return 0
        if msg in (0x0201, 0x0203):
            x = ctypes.c_short(lparam & 0xFFFF).value
            y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            self._left_down.value = 1
            self._left_drag.value = 0
            self._press_x.value = x
            self._press_y.value = y
            self._cur_x.value = x
            self._cur_y.value = y
            self._press_n.value += 1
            if self.gl is not None:
                self.gl.SetCapture(hwnd)
            return 0
        if msg == 0x0202:
            if self._left_drag.value:
                self._drop[0] = self._press_x.value
                self._drop[1] = self._press_y.value
                self._drop[2] = self._cur_x.value
                self._drop[3] = self._cur_y.value
                self._drop_n.value = 1
            else:
                count = self._click_n.value
                if count < 32:
                    self._click_x[count] = self._press_x.value
                    self._click_y[count] = self._press_y.value
                    self._click_n.value = count + 1
            self._left_down.value = 0
            self._left_drag.value = 0
            if self.gl is not None:
                self.gl.ReleaseCapture()
            return 0
        if msg == 0x0200 and self._left_down.value:
            x = ctypes.c_short(lparam & 0xFFFF).value
            y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            self._cur_x.value = x
            self._cur_y.value = y
            if abs(x - self._press_x.value) > 6 or abs(y - self._press_y.value) > 6:
                self._left_drag.value = 1
            return 0
        if self.gl is not None and self.gl._old_proc:
            return self.gl.CallWindowProcW(self.gl._old_proc, hwnd, msg, wparam, lparam)
        return 0

    def _ensure(self) -> None:
        if self.gl is not None:
            return
        if self.winfo_width() < 10 or not self.winfo_ismapped():
            self.after(30, self._ensure)
            return
        self.gl = GL()
        self.gl.attach(self.winfo_id(), self.winfo_width(), self.winfo_height(), self._wndproc)
        self.gl.place(self.winfo_width(), self.winfo_height())
        self.meshes = build_meshes()
        self._load_assets()
        self._compile_pieces()
        self._ready = True
        self.redraw()

    def _load_assets(self) -> None:
        assert self.gl is not None
        self.gl.make_current()
        styles = ASSETS / "Styles"
        for name in ("Wood", "Marble", "Metal", "Grass", "Fur"):
            folder = styles / name
            if (folder / "Board.plist").exists():
                self.materials[("board", name)] = self._plist(folder / "Board.plist")
            if (folder / "Piece.plist").exists():
                self.materials[("piece", name)] = self._plist(folder / "Piece.plist")
            for key, filename in (
                ("light", "WhiteBoard.png"),
                ("dark", "BlackBoard.png"),
                ("border", "Border.png"),
                ("white", "WhitePiece.png"),
                ("black", "BlackPiece.png"),
            ):
                path = folder / filename
                if path.exists():
                    self.textures[(name, key)] = self._upload(path, key in ("white", "black"))
        selected = ASSETS / "selected_piece_texture.png"
        if selected.exists():
            self.textures[("selected", "piece")] = self._upload(selected, True)
        self._make_labels()

    def _plist(self, path: Path) -> dict:
        import plistlib
        with path.open("rb") as handle:
            return plistlib.load(handle)

    def _upload(self, path: Path, wrap_blend: bool = False) -> int:
        """sub_1000171C5. Piece textures are blended with their mirror image
        so the seam where the lathe wraps around does not show."""
        image = Image.open(path).convert("RGBA")
        if wrap_blend:
            width, _height = image.size
            half = max(width >> 1, 1)
            weights = []
            for x in range(width):
                edge = x if x < half else width - 1 - x
                weights.append(int(round(255 * ((edge * 0.5) / half + 0.5))))
            mask = Image.new("L", (width, 1))
            mask.putdata(weights)
            mask = mask.resize(image.size, Image.NEAREST)
            image = Image.composite(image, image.transpose(Image.FLIP_LEFT_RIGHT), mask)
        image = image.transpose(Image.FLIP_TOP_BOTTOM)
        width, height = image.size
        data = image.tobytes()
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        tex = ctypes.c_uint()
        self.gl.glGenTextures(1, ctypes.byref(tex))
        name = tex.value
        self.gl.glBindTexture(GL_TEXTURE_2D, name)
        self.gl.gluBuild2DMipmaps(GL_TEXTURE_2D, GL_RGBA, width, height, GL_RGBA, GL_UNSIGNED_BYTE, ctypes.cast(buf, ctypes.c_void_p))
        self._texture_params()
        return name

    def _texture_params(self) -> None:
        # The original builds mipmaps but samples with GL_LINEAR, and caps
        # the anisotropy at 4 in initWithFrame:.
        self.gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        self.gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        self.gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT)
        self.gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT)
        extensions = self.gl.glGetString(GL_EXTENSIONS) or b""
        if b"GL_EXT_texture_filter_anisotropic" in extensions:
            limit = ctypes.c_float(1.0)
            self.gl.glGetFloatv(GL_MAX_TEXTURE_MAX_ANISOTROPY, ctypes.byref(limit))
            self.gl.glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY, min(4.0, max(limit.value, 1.0)))

    def _make_labels(self) -> None:
        """sub_100016E98: a 128x128 alpha texture holding one glyph drawn in
        Arial Rounded MT Bold at 140 points, 25% opaque, centred on its
        advance with the baseline 10 pixels above the bottom. The rows stay
        top first, as CGBitmapContext stores them."""
        font = None
        for name in ("ARLRDBD.TTF", "arialbd.ttf", "arial.ttf"):
            path = Path(r"C:\Windows\Fonts") / name
            if path.exists():
                font = ImageFont.truetype(str(path), 140)
                break
        if font is None:
            font = ImageFont.load_default()
        for text in list("ABCDEFGH12345678"):
            image = Image.new("L", (128, 128), 0)
            draw = ImageDraw.Draw(image)
            draw.text((64, 118), text, fill=64, font=font, anchor="ms")
            data = image.tobytes()
            buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
            tex = ctypes.c_uint()
            self.gl.glGenTextures(1, ctypes.byref(tex))
            self.gl.glBindTexture(GL_TEXTURE_2D, tex.value)
            self.gl.glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
            self.gl.gluBuild2DMipmaps(GL_TEXTURE_2D, GL_ALPHA, 128, 128, GL_ALPHA, GL_UNSIGNED_BYTE, ctypes.cast(buf, ctypes.c_void_p))
            self._texture_params()
            self.labels[text] = tex.value

    def _compile_pieces(self) -> None:
        gl = self.gl
        for kind, batches in self.meshes.items():
            list_id = gl.glGenLists(1)
            gl.glNewList(list_id, GL_COMPILE)
            for mode, verts in batches:
                if mode == "fan":
                    primitive = GL_TRIANGLE_STRIP
                    order = (verts[0], verts[1], verts[3], verts[2])
                elif mode == "strip":
                    primitive = GL_TRIANGLE_STRIP
                    order = verts
                elif mode == "quad":
                    primitive = GL_QUADS
                    order = verts
                else:
                    primitive = GL_TRIANGLES
                    order = verts
                gl.glBegin(primitive)
                for x, y, z, nx, ny, nz, u, v in order:
                    gl.glNormal3f(nx, ny, nz)
                    gl.glTexCoord2f(u, v)
                    gl.glVertex3f(x, y, z)
                gl.glEnd()
            gl.glEndList()
            self.piece_lists[kind] = list_id

    def redraw(self) -> None:
        if not self._ready or self.gl is None or self.winfo_width() < 10:
            return
        try:
            self.gl.make_current()
            self._paint()
            self.gl.swap()
        except Exception as exc:
            self._error = str(exc)

    def _camera(self, width: int, height: int) -> None:
        variant = 0 if self.scene.pos is None else self.scene.pos.variant
        scale = 1.15 if variant == 1 else 1.0
        fov = math.degrees(2.0 * math.atan2(max(1.1 * height * scale / width, 1.0) * 50.0, 300.0))
        gl = self.gl
        gl.glMatrixMode(GL_PROJECTION)
        gl.glLoadIdentity()
        gl.gluPerspective(fov, width / height, 10.0, 1000.0)
        elevation = math.radians(self.scene.elevation)
        azimuth = math.radians(self.scene.azimuth)
        eye_x = math.sin(azimuth) * math.cos(elevation) * 300.0
        eye_y = math.sin(elevation) * 300.0
        eye_z = -math.cos(azimuth) * math.cos(elevation) * 300.0
        gl.glMatrixMode(GL_MODELVIEW)
        gl.glLoadIdentity()
        gl.gluLookAt(eye_x, eye_y, eye_z, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
        self._eye = (eye_x, eye_y, eye_z)
        self._fov = fov
        self._aspect = width / height
        forward = _norm(-eye_x, -eye_y, -eye_z)
        right = _norm(
            forward[1] * 0.0 - forward[2] * 1.0,
            forward[2] * 0.0 - forward[0] * 0.0,
            forward[0] * 1.0 - forward[1] * 0.0,
        )
        up = (
            right[1] * forward[2] - right[2] * forward[1],
            right[2] * forward[0] - right[0] * forward[2],
            right[0] * forward[1] - right[1] * forward[0],
        )
        self._forward = forward
        self._right = right
        self._up = up

    def _reflectivity(self) -> float:
        # initWithFrame: sets 0.3; loadStyles: replaces it from Board.plist.
        return float(self._board_mat().get("Reflectivity", 0.3))

    def _paint(self) -> None:
        """-[MBCBoardView drawPosition] with setupPerspective's GL state."""
        gl = self.gl
        width, height = gl.client_size()
        self.view_w, self.view_h = width, height
        gl.glViewport(0, 0, width, height)
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glEnable(GL_DEPTH_TEST)
        gl.glEnable(GL_NORMALIZE)
        gl.glEnable(GL_TEXTURE_2D)
        gl.glEnable(GL_LIGHTING)
        gl.glEnable(GL_MULTISAMPLE)
        # The original culls back faces. The port's lathe strips are not
        # wound consistently, so culling stays off; closed pieces look the same.
        gl.glDisable(GL_CULL_FACE)
        gl.glLightModeli(GL_LIGHT_MODEL_LOCAL_VIEWER, 1)
        gl.glLightModeli(GL_LIGHT_MODEL_COLOR_CONTROL, GL_SEPARATE_SPECULAR_COLOR)
        gl.glShadeModel(GL_SMOOTH)
        gl.glHint(GL_PERSPECTIVE_CORRECTION_HINT, GL_NICEST)
        gl.glColorMask(1, 1, 1, 1)
        gl.glDepthMask(1)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT | GL_STENCIL_BUFFER_BIT)
        self._draw_backdrop(width, height)
        self._camera(width, height)
        self._light()
        self._draw_board(1.0)
        reflectivity = self._reflectivity()
        if reflectivity > 0.0:
            self._reflection(reflectivity)
            self._draw_board(1.0 - reflectivity, blended=True)
        self._draw_labels()
        self._draw_move_arrow(self.scene.hint_move, True)
        self._draw_move_arrow(self.scene.last_move, False)
        gl.glDisable(GL_BLEND)
        if self.scene.pos is not None and self.scene.pos.variant == 1:
            self._draw_hand()
        self._draw_pieces(1.0, reflected=False)
        self._draw_selected(False)
        self._draw_promotion_piece()
        if self._manip is not None:
            self._draw_manipulator(width, height)

    def _ortho(self, width: int, height: int) -> None:
        gl = self.gl
        gl.glMatrixMode(GL_PROJECTION)
        gl.glPushMatrix()
        gl.glLoadIdentity()
        gl.glOrtho(0.0, float(width), 0.0, float(height), -1.0, 1.0)
        gl.glMatrixMode(GL_MODELVIEW)
        gl.glPushMatrix()
        gl.glLoadIdentity()

    def _end_ortho(self) -> None:
        gl = self.gl
        gl.glPopMatrix()
        gl.glMatrixMode(GL_PROJECTION)
        gl.glPopMatrix()
        gl.glMatrixMode(GL_MODELVIEW)

    def _draw_backdrop(self, width: int, height: int) -> None:
        """-[MBCBoardView drawGloss]: a 0.25 grey field with a curved band
        shading from 0.3 to 0.6 grey up to the top of the view."""
        gl = self.gl
        self._ortho(width, height)
        gl.glDisable(GL_DEPTH_TEST)
        gl.glDisable(GL_TEXTURE_2D)
        gl.glDisable(GL_LIGHTING)
        gl.glDisable(GL_BLEND)
        w, h = float(width), float(height)
        gl.glColor3f(0.25, 0.25, 0.25)
        gl.glBegin(GL_QUADS)
        gl.glVertex2f(0.0, 0.0)
        gl.glVertex2f(w, 0.0)
        gl.glVertex2f(w, h)
        gl.glVertex2f(0.0, h)
        gl.glEnd()
        centre_y = (w / h) * (3.0 * h)
        half = w * 0.5
        radius_sq = math.hypot(half, h * -0.55 + centre_y) ** 2
        gl.glBegin(GL_TRIANGLE_STRIP)
        for step in range(33):
            x = w * step * 0.03125
            gl.glColor3f(0.3, 0.3, 0.3)
            gl.glVertex2f(x, centre_y - math.sqrt(max(radius_sq - (half - x) ** 2, 0.0)))
            gl.glColor3f(0.6, 0.6, 0.6)
            gl.glVertex2f(x, h)
        gl.glEnd()
        self._end_ortho()
        gl.glEnable(GL_DEPTH_TEST)
        gl.glEnable(GL_TEXTURE_2D)
        gl.glEnable(GL_LIGHTING)

    def _light(self) -> None:
        """-[MBCBoardView placeLights]: a directional key light, and a
        spotlight from 100 units above the picked square."""
        gl = self.gl
        bright = (ctypes.c_float * 4)(0.75, 0.75, 0.75, 1.0)
        ambient = (ctypes.c_float * 4)(0.125, 0.125, 0.125, 0.0)
        position = (ctypes.c_float * 4)(-60.0, 200.0, 0.0, 0.0)
        model_ambient = (ctypes.c_float * 4)(0.2, 0.2, 0.2, 1.0)
        gl.glLightModelfv(GL_LIGHT_MODEL_AMBIENT, model_ambient)
        gl.glEnable(GL_LIGHT0)
        gl.glLightfv(GL_LIGHT0, GL_DIFFUSE, bright)
        gl.glLightfv(GL_LIGHT0, GL_AMBIENT, ambient)
        gl.glLightfv(GL_LIGHT0, GL_SPECULAR, bright)
        gl.glLightfv(GL_LIGHT0, GL_POSITION, position)
        picked = self.scene.picked
        if picked is None:
            gl.glDisable(GL_LIGHT1)
            return
        x, _y, z = square_center(picked)
        white = (ctypes.c_float * 4)(1.0, 1.0, 1.0, 1.0)
        spot_pos = (ctypes.c_float * 4)(x, 100.0, z, 1.0)
        down = (ctypes.c_float * 3)(0.0, -1.0, 0.0)
        gl.glEnable(GL_LIGHT1)
        gl.glLightfv(GL_LIGHT1, GL_DIFFUSE, white)
        gl.glLightfv(GL_LIGHT1, GL_SPECULAR, white)
        gl.glLightfv(GL_LIGHT1, GL_POSITION, spot_pos)
        gl.glLightfv(GL_LIGHT1, GL_SPOT_DIRECTION, down)
        gl.glLighti(GL_LIGHT1, GL_SPOT_EXPONENT, 100)
        gl.glLighti(GL_LIGHT1, GL_SPOT_CUTOFF, 5)
        gl.glLightf(GL_LIGHT1, GL_CONSTANT_ATTENUATION, 0.0)
        gl.glLightf(GL_LIGHT1, GL_QUADRATIC_ATTENUATION, 0.0001)

    def _board_mat(self) -> dict:
        name = self.scene.board_style
        return self.materials.get(("board", name), self.materials.get(("board", "Wood"), {}))

    def _bind_style(self, style: str, key: str, alpha: float, which: str) -> None:
        """-[MBCDrawStyle startStyle:] with initWithTexture:'s defaults
        (diffuse 1, specular 0.2, shininess 5, alpha 1) under the plist."""
        mat = self.materials.get((which, style), {})
        prefix = {"light": "White", "white": "White", "dark": "Black", "black": "Black", "border": "Border"}.get(key, "White")
        diffuse = float(mat.get(prefix + "Diffuse", 1.0))
        specular = float(mat.get(prefix + "Specular", 0.2))
        shine = float(mat.get(prefix + "Shininess", 5.0))
        base_alpha = float(mat.get(prefix + "Alpha", 1.0)) * alpha
        self._material(diffuse, specular, shine, base_alpha)
        tex = self.textures.get((style, key)) or self.textures.get(("Wood", key), 0)
        self.gl.glBindTexture(GL_TEXTURE_2D, tex)

    def _material(self, diffuse: float, specular: float, shine: float, alpha: float) -> None:
        gl = self.gl
        color = (ctypes.c_float * 4)(diffuse, diffuse, diffuse, alpha)
        spec = (ctypes.c_float * 4)(specular, specular, specular, alpha)
        emission = (ctypes.c_float * 4)(0.0, 0.0, 0.0, alpha)
        gl.glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        gl.glMaterialfv(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE, color)
        gl.glMaterialfv(GL_FRONT_AND_BACK, GL_EMISSION, emission)
        gl.glMaterialfv(GL_FRONT_AND_BACK, GL_SPECULAR, spec)
        gl.glMaterialf(GL_FRONT_AND_BACK, GL_SHININESS, shine)

    def _draw_board(self, alpha: float, blended: bool = False) -> None:
        """-[MBCBoardView drawBoard:]. The mirror pass (blended) runs with
        the depth test off and leaves the border alone."""
        gl = self.gl
        gl.glEnable(GL_TEXTURE_2D)
        gl.glEnable(GL_LIGHTING)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        if blended:
            gl.glDisable(GL_DEPTH_TEST)
        style = self.scene.board_style if ("board", self.scene.board_style) in self.materials else "Wood"
        gl.glNormal3f(0.0, 1.0, 0.0)
        for file in range(8):
            for rank in range(8):
                key = "dark" if ((file ^ rank) & 1) == 0 else "light"
                self._bind_style(style, key, alpha, "board")
                s = self._offsets[file * 8 + rank]
                x0 = -40.0 + file * 10.0
                z0 = 40.0 - rank * 10.0
                gl.glBegin(GL_TRIANGLE_STRIP)
                for u, v, x, z in (
                    (s, s, x0, z0),
                    (s + 0.5, s, x0 + 10.0, z0),
                    (s, s + 0.5, x0, z0 - 10.0),
                    (s + 0.5, s + 0.5, x0 + 10.0, z0 - 10.0),
                ):
                    gl.glTexCoord2f(u, -v)
                    gl.glVertex3f(x, 0.0, z)
                gl.glEnd()
        if not blended:
            self._bind_style(style, "border", 1.0, "board")
            self._border()
        else:
            gl.glEnable(GL_DEPTH_TEST)
        gl.glDisable(GL_BLEND)

    def _border(self) -> None:
        """The eight strips of drawBoard:, with their texture coordinates."""
        gl = self.gl
        a, b = 0.067567557, 0.93243241
        strips = [
            ((0, 1, 0), [(0, 0, -46.25, 0, 46.25), (a, 0, -40, 0, 46.25), (a, 1, -40, 0, 40), (b, 0, 40, 0, 46.25), (b, 1, 40, 0, 40), (1, 0, 46.25, 0, 46.25)]),
            ((0, 0, 1), [(0, 1, -46.25, 0, 46.25), (0, 0, -46.25, -5, 46.25), (1, 1, 46.25, 0, 46.25), (1, 0, 46.25, -5, 46.25)]),
            ((0, 1, 0), [(0, 0, -46.25, 0, -46.25), (a, 1, -40, 0, -40), (a, 0, -40, 0, -46.25), (b, 1, 40, 0, -40), (b, 0, 40, 0, -46.25), (1, 0, 46.25, 0, -46.25)]),
            ((0, 0, -1), [(0, 1, -46.25, 0, -46.25), (1, 1, 46.25, 0, -46.25), (0, 0, -46.25, -5, -46.25), (1, 0, 46.25, -5, -46.25)]),
            ((0, 1, 0), [(1, 0, -46.25, 0, 46.25), (b, 1, -40, 0, 40), (b, 0, -46.25, 0, 40), (a, 1, -40, 0, -40), (a, 0, -46.25, 0, -40), (0, 0, -46.25, 0, -46.25)]),
            ((-1, 0, 0), [(1, 1, -46.25, 0, 46.25), (0, 1, -46.25, 0, -46.25), (1, 0, -46.25, -5, 46.25), (0, 0, -46.25, -5, -46.25)]),
            ((0, 1, 0), [(0, 0, 46.25, 0, 46.25), (a, 0, 46.25, 0, 40), (a, 1, 40, 0, 40), (b, 0, 46.25, 0, -40), (b, 1, 40, 0, -40), (1, 0, 46.25, 0, -46.25)]),
            ((1, 0, 0), [(0, 1, 46.25, 0, 46.25), (0, 0, 46.25, -5, 46.25), (1, 1, 46.25, 0, -46.25), (1, 0, 46.25, -5, -46.25)]),
        ]
        for normal, verts in strips:
            gl.glBegin(GL_TRIANGLE_STRIP)
            gl.glNormal3f(*normal)
            for u, v, x, y, z in verts:
                gl.glTexCoord2f(u, -v)
                gl.glVertex3f(x, y, z)
            gl.glEnd()

    def _reflection(self, reflectivity: float) -> None:
        """The mirror block of drawPosition: a stencil of the squares, then
        the pieces flipped under the board at alpha Reflectivity, drawn with
        the depth test still off."""
        gl = self.gl
        gl.glDisable(GL_DEPTH_TEST)
        gl.glDisable(GL_LIGHTING)
        gl.glDisable(GL_BLEND)
        gl.glDisable(GL_TEXTURE_2D)
        gl.glColorMask(0, 0, 0, 0)
        gl.glDepthMask(0)
        gl.glEnable(GL_STENCIL_TEST)
        gl.glStencilMask(0xFFFFFFFF)
        gl.glStencilOp(GL_KEEP, GL_KEEP, GL_REPLACE)
        gl.glStencilFunc(GL_ALWAYS, 1, 0xFFFFFFFF)
        gl.glBegin(GL_QUADS)
        gl.glVertex3f(-40, 0, 40)
        gl.glVertex3f(40, 0, 40)
        gl.glVertex3f(40, 0, -40)
        gl.glVertex3f(-40, 0, -40)
        gl.glEnd()
        gl.glColorMask(1, 1, 1, 0)
        gl.glDepthMask(1)
        gl.glStencilFunc(GL_EQUAL, 1, 0xFFFFFFFF)
        gl.glStencilOp(GL_KEEP, GL_KEEP, GL_KEEP)
        gl.glEnable(GL_TEXTURE_2D)
        gl.glPushMatrix()
        gl.glScalef(1.0, -1.0, 1.0)
        gl.glEnable(GL_LIGHTING)
        self._light()
        self._draw_pieces(1.0, reflected=True, depth_test=False)
        self._draw_selected(True, depth_test=False)
        gl.glPopMatrix()
        gl.glDisable(GL_STENCIL_TEST)
        gl.glColorMask(1, 1, 1, 1)
        gl.glEnable(GL_DEPTH_TEST)
        self._light()

    def _piece_style(self) -> str:
        return self.scene.piece_style if (self.scene.piece_style, "white") in self.textures else "Wood"

    def _draw_pieces(self, alpha: float, reflected: bool, depth_test: bool = True) -> None:
        """-[MBCBoardView drawPieces:]. While a piece is dragged, pieces
        within 5 units of it fade to (distance / 5) ** 4; during a move
        animation only the piece on the target square fades."""
        pos = self.scene.pos
        if pos is None:
            return
        anim = getattr(self.scene, "anim", None)
        drag = self.scene.drag
        for sq, piece in enumerate(pos.board):
            if not piece:
                continue
            if drag is not None and sq == drag[0]:
                continue
            origin = square_center(sq)
            fade = alpha
            if drag is not None and (anim is None or sq == anim):
                distance = math.hypot(drag[1] - origin[0], drag[2] - origin[2])
                if distance < 5.0:
                    fade = alpha * (distance / 5.0) ** 4
            self._piece(piece, origin, 1.0, fade, reflected, depth_test)

    def _piece(self, piece: int, origin: tuple[float, float, float], scale: float, alpha: float,
               reflected: bool = False, depth_test: bool = True) -> None:
        """setupPieceDrawing:reflect:alpha:, simplyDrawPiece:at:scale:."""
        gl = self.gl
        if reflected:
            alpha *= self._reflectivity()
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        gl.glEnable(GL_TEXTURE_2D)
        gl.glEnable(GL_LIGHTING)
        gl.glDepthMask(1 if alpha > 0.5 else 0)
        key = "white" if color_of(piece) == WHITE else "black"
        self._bind_style(self._piece_style(), key, alpha, "piece")
        self._call_piece(piece, origin, scale)
        gl.glDepthMask(1)
        gl.glDisable(GL_BLEND)

    def _call_piece(self, piece: int, origin: tuple[float, float, float], scale: float) -> None:
        gl = self.gl
        kind = kind_of(piece)
        gl.glPushMatrix()
        gl.glTranslatef(*origin)
        if kind == 4 and color_of(piece) == WHITE:
            gl.glRotatef(180.0, 0.0, 1.0, 0.0)
        gl.glScalef(scale, scale, scale)
        # Meshes are keyed by the original's list numbers (king 1 with the
        # cross, queen 2 with the crown); the rules use queen 1, king 2.
        gl.glCallList(self.piece_lists[MESH_LIST.get(kind, kind)])
        gl.glPopMatrix()

    def _draw_selected(self, reflected: bool, depth_test: bool = True) -> None:
        """drawSelectedPiece: at fSelectedPos while a piece is dragged."""
        drag = self.scene.drag
        pos = self.scene.pos
        if drag is None or pos is None:
            return
        source = drag[0]
        piece = source - HAND_BASE if source >= HAND_BASE else pos.board[source]
        if piece:
            self._piece(piece, (drag[1], 0.0, drag[2]), 1.0, 1.0, reflected, depth_test)

    def _draw_hand(self) -> None:
        """-[MBCBoardView drawPiecesInHand]: counts from 2 up on the frame
        at x 42-45, and the pieces at x 51, scaled 0.85."""
        pos = self.scene.pos
        gl = self.gl
        azimuth = self.scene.azimuth % 360.0
        t = 1.0 if (azimuth < 90.0 or azimuth >= 270.0) else 0.0
        s = 1.0 - t
        drag = self.scene.drag
        held = drag[0] - HAND_BASE if drag is not None and drag[0] >= HAND_BASE else None
        gl.glEnable(GL_TEXTURE_2D)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        gl.glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        gl.glDisable(GL_LIGHTING)
        gl.glDisable(GL_DEPTH_TEST)
        gl.glColor4f(1.0, 1.0, 1.0, float(self._board_mat().get("LabelIntensity", 1.0)))
        for side, sign in ((WHITE, 1.0), (BLACK, -1.0)):
            for index, kind in enumerate(HAND_ORDER):
                code = kind | side
                count = pos.hand[code] - (1 if held == code else 0)
                if count < 2:
                    continue
                gl.glBindTexture(GL_TEXTURE_2D, self.labels[str(min(count, 8))])
                near = sign * (6.0 + 8.0 * index)
                far = near + 3.0 * sign
                first, second = (far, near) if side == WHITE else (near, far)
                gl.glBegin(GL_QUADS)
                gl.glTexCoord2f(t, -t)
                gl.glVertex3f(42.0, 0.0, first)
                gl.glTexCoord2f(s, -t)
                gl.glVertex3f(45.0, 0.0, first)
                gl.glTexCoord2f(s, -s)
                gl.glVertex3f(45.0, 0.0, second)
                gl.glTexCoord2f(t, -s)
                gl.glVertex3f(42.0, 0.0, second)
                gl.glEnd()
        gl.glDisable(GL_BLEND)
        gl.glEnable(GL_DEPTH_TEST)
        gl.glEnable(GL_LIGHTING)
        for side, sign in ((WHITE, 1.0), (BLACK, -1.0)):
            for index, kind in enumerate(HAND_ORDER):
                code = kind | side
                if pos.hand[code] - (1 if held == code else 0) > 0:
                    self._piece(code, (51.0, 0.0, sign * (7.0 + 8.0 * index)), 0.85, 1.0)

    def _draw_promotion_piece(self) -> None:
        """-[MBCBoardView drawPromotionPiece]: the default promotion piece
        beside the board, in the selected-piece style at alpha 0.8."""
        promo = self.scene.promo_piece
        if promo is None:
            return
        side, kind = promo
        gl = self.gl
        x, z = (-50.0, -35.0) if side == WHITE else (50.0, 35.0)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        gl.glEnable(GL_TEXTURE_2D)
        gl.glEnable(GL_LIGHTING)
        self._material(1.0, 0.2, 5.0, 0.8)
        gl.glBindTexture(GL_TEXTURE_2D, self.textures.get(("selected", "piece"), 0))
        gl.glDepthMask(1)
        self._call_piece(kind | side, (x, 0.0, z), 1.0)
        gl.glDisable(GL_BLEND)

    @staticmethod
    def _arrow_tris(ax: float, az: float, bx: float, bz: float, width: float) -> list[tuple[float, float]]:
        """-[MBCBoardView drawArrowFrom:to:width:]: a shaft 2*width wide and
        a head 4*width wide and 1.5*width long."""
        dx, dz = bx - ax, bz - az
        length = math.hypot(dx, dz)
        angle = math.atan2(dz, dx)
        sn, cs = math.sin(angle), math.cos(angle)
        v0 = (ax - width * sn, az + width * cs)
        v1 = (ax + width * sn, az - width * cs)
        shaft = length - 1.5 * width
        v2 = (v0[0] + shaft * cs, v0[1] + shaft * sn)
        v3 = (v1[0] + shaft * cs, v1[1] + shaft * sn)
        v4 = (v2[0] - sn * width, v2[1] + cs * width)
        v5 = (v3[0] + sn * width, v3[1] - cs * width)
        return [v0, v1, v3, v3, v2, v0, v4, v5, (bx, bz)]

    def _draw_move_arrow(self, move, hint: bool) -> None:
        """drawMove:asHint: red for the hint, blue for the last move."""
        if move is None:
            return
        fr, to = move
        if fr is None or fr < 0 or to is None:
            return
        gl = self.gl
        ax, _ay, az = square_center(fr)
        bx, _by, bz = square_center(to)
        gl.glDisable(GL_DEPTH_TEST)
        gl.glDisable(GL_TEXTURE_2D)
        gl.glDisable(GL_LIGHTING)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        gl.glColor4f(1.0 if hint else 0.0, 0.0, 0.0 if hint else 1.0, 0.5)
        gl.glBegin(GL_TRIANGLES)
        for x, z in self._arrow_tris(ax, az, bx, bz, 2.0):
            gl.glVertex3f(x, 0.01, z)
        gl.glEnd()
        gl.glEnable(GL_DEPTH_TEST)
        gl.glEnable(GL_TEXTURE_2D)
        gl.glEnable(GL_LIGHTING)

    def _draw_labels(self) -> None:
        """-[MBCBoardView drawCoords]: 1-8 down the left edge and A-H along
        the edge nearest the viewer, 3.5 units tall, white at LabelIntensity."""
        if not self.labels:
            return
        gl = self.gl
        gl.glEnable(GL_TEXTURE_2D)
        gl.glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        gl.glEnable(GL_BLEND)
        gl.glDisable(GL_DEPTH_TEST)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        gl.glDisable(GL_LIGHTING)
        gl.glColor4f(1.0, 1.0, 1.0, float(self._board_mat().get("LabelIntensity", 1.0)))
        azimuth = self.scene.azimuth % 360.0
        facing_white = 90.0 < azimuth <= 270.0
        for i in range(8):
            near = 36.75 - 10.0 * i
            far = near - 3.5
            if facing_white:
                x0, x1, z0, z1 = -44.75, -41.25, near, far
            else:
                x0, x1, z0, z1 = -41.25, -44.75, far, near
            self._quad(self.labels[str(i + 1)], ((x0, z0), (x1, z0), (x1, z1), (x0, z1)))
        for j in range(8):
            left = 10.0 * j - 36.75
            right = left + 3.5
            if facing_white:
                u0, u1, zv0, zv1 = left, right, 44.5, 41.0
            else:
                u0, u1, zv0, zv1 = right, left, -44.5, -41.0
            self._quad(self.labels["ABCDEFGH"[j]], ((u0, zv0), (u1, zv0), (u1, zv1), (u0, zv1)))
        gl.glDisable(GL_BLEND)
        gl.glEnable(GL_DEPTH_TEST)
        gl.glEnable(GL_LIGHTING)

    def _quad(self, texture: int, corners) -> None:
        gl = self.gl
        gl.glBindTexture(GL_TEXTURE_2D, texture)
        gl.glBegin(GL_QUADS)
        for (u, v), (x, z) in zip(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)), corners):
            gl.glTexCoord2f(u, -v)
            gl.glVertex3f(x, 0.0, z)
        gl.glEnd()

    def _draw_manipulator(self, width: int, height: int) -> None:
        """-[MBCBoardView drawManipulator]: a grey rounded cross at the
        press point with a centre dot and four arrows that light up in the
        direction of the drag."""
        gl = self.gl
        scale = self.winfo_fpixels("1i") / 72.0
        ox = float(self._manip["orig"][0])
        oy = float(height - self._manip["orig"][1])
        cx = float(self._cur_x.value)
        cy = float(height - self._cur_y.value)
        dx, dy = abs(cx - ox), abs(cy - oy)
        grey = (0.5, 0.5, 0.5, 0.6)
        dim = (1.0, 1.0, 1.0, 0.4)
        lit = (1.0, 1.0, 1.0, 1.0)
        self._ortho(width, height)
        gl.glDisable(GL_DEPTH_TEST)
        gl.glDisable(GL_TEXTURE_2D)
        gl.glDisable(GL_LIGHTING)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        def point(x: float, y: float) -> None:
            gl.glVertex2f(ox + x * scale, oy + y * scale)

        def disk(cx0: float, cy0: float, radius: float, start: float, sweep: float, slices: int) -> None:
            gl.glBegin(GL_TRIANGLE_FAN)
            point(cx0, cy0)
            for step in range(slices + 1):
                ang = math.radians(start + sweep * step / slices)
                point(cx0 + radius * math.sin(ang), cy0 + radius * math.cos(ang))
            gl.glEnd()

        gl.glColor4f(*grey)
        gl.glBegin(GL_QUADS)
        for x, y in ((-35, -55), (35, -55), (35, -35), (-35, -35),
                     (-55, -35), (55, -35), (55, 35), (-55, 35),
                     (-35, 35), (35, 35), (35, 55), (-35, 55)):
            point(x, y)
        gl.glEnd()
        disk(-35, -35, 20, 180, 90, 10)
        disk(35, -35, 20, 90, 90, 10)
        disk(35, 35, 20, 0, 90, 10)
        disk(-35, 35, 20, 270, 90, 10)
        gl.glColor4f(*(dim if dx >= 10 or dy >= 10 else lit))
        disk(0, 0, 10, 0, 360, 40)
        arrows = (
            ((0, 15), (0, 45), not (dx > dy or cy <= oy + 10)),
            ((0, -15), (0, -45), not (dx > dy or oy - 10 <= cy)),
            ((15, 0), (45, 0), not (dx <= dy or cx <= ox + 10)),
            ((-15, 0), (-45, 0), not (dx <= dy or ox - 10 <= cx)),
        )
        for (ax, ay), (bx, by), active in arrows:
            gl.glColor4f(*(lit if active else dim))
            gl.glBegin(GL_TRIANGLES)
            for x, y in self._arrow_tris(ax, ay, bx, by, 10.0):
                point(x, y)
            gl.glEnd()
        gl.glDisable(GL_BLEND)
        self._end_ortho()
        gl.glEnable(GL_DEPTH_TEST)
        gl.glEnable(GL_TEXTURE_2D)
        gl.glEnable(GL_LIGHTING)

    def pick(self, px: int, py: int) -> int | None:
        if not hasattr(self, "_eye"):
            return None
        width = max(self.view_w, 1)
        height = max(self.view_h, 1)
        eye = self._eye
        forward = _norm(-eye[0], -eye[1], -eye[2])
        world_up = (0.0, 1.0, 0.0)
        right = _norm(
            forward[1] * world_up[2] - forward[2] * world_up[1],
            forward[2] * world_up[0] - forward[0] * world_up[2],
            forward[0] * world_up[1] - forward[1] * world_up[0],
        )
        cup = (
            right[1] * forward[2] - right[2] * forward[1],
            right[2] * forward[0] - right[0] * forward[2],
            right[0] * forward[1] - right[1] * forward[0],
        )
        tany = math.tan(math.radians(self._fov) * 0.5)
        tanx = tany * self._aspect
        nx = (px / width) * 2.0 - 1.0
        ny = 1.0 - (py / height) * 2.0
        direction = _norm(
            forward[0] + right[0] * nx * tanx + cup[0] * ny * tany,
            forward[1] + right[1] * nx * tanx + cup[1] * ny * tany,
            forward[2] + right[2] * nx * tanx + cup[2] * ny * tany,
        )
        if abs(direction[1]) < 1e-5:
            return None
        distance = -eye[1] / direction[1]
        if distance <= 0:
            return None
        x = eye[0] + direction[0] * distance
        z = eye[2] + direction[2] * distance
        file = int((x + 40.0) // 10.0)
        rank = int((40.0 - z) // 10.0)
        if 0 <= file < 8 and 0 <= rank < 8:
            return rank * 8 + file
        return None

    def hit_point(self, px: int, py: int) -> tuple[float, float] | None:
        square = self.pick(px, py)
        if not hasattr(self, "_eye"):
            return None
        width = max(self.view_w, 1)
        height = max(self.view_h, 1)
        eye = self._eye
        forward = _norm(-eye[0], -eye[1], -eye[2])
        world_up = (0.0, 1.0, 0.0)
        right = _norm(
            forward[1] * world_up[2] - forward[2] * world_up[1],
            forward[2] * world_up[0] - forward[0] * world_up[2],
            forward[0] * world_up[1] - forward[1] * world_up[0],
        )
        cup = (
            right[1] * forward[2] - right[2] * forward[1],
            right[2] * forward[0] - right[0] * forward[2],
            right[0] * forward[1] - right[1] * forward[0],
        )
        tany = math.tan(math.radians(self._fov) * 0.5)
        tanx = tany * self._aspect
        nx = (px / width) * 2.0 - 1.0
        ny = 1.0 - (py / height) * 2.0
        direction = _norm(
            forward[0] + right[0] * nx * tanx + cup[0] * ny * tany,
            forward[1] + right[1] * nx * tanx + cup[1] * ny * tany,
            forward[2] + right[2] * nx * tanx + cup[2] * ny * tany,
        )
        if abs(direction[1]) < 1e-5:
            return None
        distance = -eye[1] / direction[1]
        if distance <= 0:
            return None
        return eye[0] + direction[0] * distance, eye[2] + direction[2] * distance if square is not None or True else None

    def project(self, x: float, y: float, z: float) -> tuple[float, float] | None:
        if not hasattr(self, "_forward"):
            return None
        eye = self._eye
        rel = (x - eye[0], y - eye[1], z - eye[2])
        depth = rel[0] * self._forward[0] + rel[1] * self._forward[1] + rel[2] * self._forward[2]
        if depth <= 1.0:
            return None
        horizontal = rel[0] * self._right[0] + rel[1] * self._right[1] + rel[2] * self._right[2]
        vertical = rel[0] * self._up[0] + rel[1] * self._up[1] + rel[2] * self._up[2]
        tany = math.tan(math.radians(self._fov) * 0.5)
        tanx = tany * self._aspect
        nx = horizontal / (depth * tanx)
        ny = vertical / (depth * tany)
        return (nx + 1.0) * 0.5 * self.view_w, (1.0 - ny) * 0.5 * self.view_h

    def _click(self, event) -> None:
        sq = self.pick(event.x, event.y)
        if sq is not None:
            self.on_square(sq)

    def _press(self, event) -> None:
        self._drag = (event.x, event.y)

    def _drag_move(self, event) -> None:
        if not self._drag:
            return
        dx = event.x - self._drag[0]
        dy = event.y - self._drag[1]
        self._drag = (event.x, event.y)
        self.on_orbit(-dx * 0.4, -dy * 0.25)
