# NEXT BEAST: Dark mode + Full Moon highlighting (canonical-ish)
# - Full moon is defined by GEOCENTRIC ecliptic longitude opposition:
#       Δλ = (λ_moon - λ_sun) wrapped to (-180°, +180°], full moon at Δλ = 180° (i.e., Δλ = -180°)
#   We detect events as minima of cos(Δλ) (since cos is -1 at 180°), then refine with a hi-res query.
#
# - Full moon "duration" highlights match reality:
#   Instead of a fixed ±12h window, we highlight the time when the Moon is within
#   FULLNESS_DEG of exact opposition (|wrap(Δλ - 180°)| <= FULLNESS_DEG).
#   This naturally varies: faster near perigee => shorter, slower near apogee => longer.
#
# - Apsidal cycle remains from robust e-vector secular fit (Theil–Sen vs LS).
# - Still chunked, MaskedColumn-safe, and JD-float single epoch for "today".

import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timezone, timedelta
from astroquery.jplhorizons import Horizons

# ----------------------------
# Constants
# ----------------------------
AU_KM = 149597870.700
DAY_S = 86400.0
MU_EARTH = 398600.4418  # km^3/s^2

# ----------------------------
# Controls
# ----------------------------
FIG_W, FIG_H, DPI = 37.5, 4, 150
REFPLANE = "ecliptic"
ABERR = "geometric"

FIT_START = "2008-01-01"
FIT_STOP  = "2036-01-01"

FIND_STEP = "60 m"        # perigee detection cadence
PLOT_STEP = "60 m"        # keep at 60m so "real duration" bands look good

CHUNK_DAYS = 180
MIN_SEP_DAYS = 10.0

# Expected apsidal slope ~ 0.71 rad/yr
SLOPE_MIN = 0.3
SLOPE_MAX = 1.2
TS_MAX_POINTS = 220
TS_MAX_PAIRS  = 40000
AGREE_REL = 0.06

# Full moon definition + duration realism:
# Full moon event occurs at Δλ = 180° (mod 360°), i.e., wrap_to_180(Δλ) = -180°.
# Full moon "duration" = times when angular separation from exact opposition <= FULLNESS_DEG.
FULLNESS_DEG = 6.0         # try 4.0 for tighter, 8.0 for broader "full-looking" duration

# Refine each full-moon time by hi-res scan around coarse estimate
REFINE_HALF_WINDOW_DAYS = 0.9
REFINE_STEP_MIN = 5

# Plot colors
COLOR_BG = "black"
COLOR_FG = "white"
COLOR_MOON = "#8ec7ff"
COLOR_FULL = "#ffe600"
COLOR_DOT  = "red"

# ----------------------------
# Helpers
# ----------------------------
def _col_to_float_array(col):
    if hasattr(col, "filled"):
        col = col.filled(np.nan)
    return np.asarray(col, dtype=float)

def utc_to_jd(dt):
    year = dt.year
    month = dt.month
    day = dt.day + (dt.hour + (dt.minute + dt.second/60.0)/60.0)/24.0
    if month <= 2:
        year -= 1
        month += 12
    A = year // 100
    B = 2 - A + (A // 4)
    jd = int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + B - 1524.5
    return float(jd)

def jd_to_utc_datetime(jd):
    jd = float(jd) + 0.5
    Z = int(jd)
    F = jd - Z
    if Z < 2299161:
        A = Z
    else:
        alpha = int((Z - 1867216.25) / 36524.25)
        A = Z + 1 + alpha - int(alpha / 4)
    B = A + 1524
    C = int((B - 122.1) / 365.25)
    D = int(365.25 * C)
    E = int((B - D) / 30.6001)
    day = B - D - int(30.6001 * E) + F
    month = E - 1 if E < 14 else E - 13
    year = C - 4716 if month > 2 else C - 4715
    day_int = int(day)
    frac = day - day_int
    seconds = int(round(frac * 86400.0))
    hh = seconds // 3600
    mm = (seconds % 3600) // 60
    ss = seconds % 60
    return datetime(year, month, day_int, hh, mm, ss, tzinfo=timezone.utc)

def parse_utc_date(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)

def wrap_to_2pi(rad):
    return (rad + 2*np.pi) % (2*np.pi)

def wrap_to_pi(rad):
    return (rad + np.pi) % (2*np.pi) - np.pi

def wrap_deg_180(deg):
    return (deg + 180.0) % 360.0 - 180.0

# ----------------------------
# HORIZONS fetchers
# ----------------------------
def horizons_vectors(body_id, start, stop, step, refplane=REFPLANE, aberr=ABERR):
    obj = Horizons(
        id=str(body_id),
        location="500@399",
        epochs={"start": start, "stop": stop, "step": step},
    )
    vec = obj.vectors(refplane=refplane, aberrations=aberr)

    jd = _col_to_float_array(vec["datetime_jd"])
    x  = _col_to_float_array(vec["x"])
    y  = _col_to_float_array(vec["y"])
    z  = _col_to_float_array(vec["z"])
    vx = _col_to_float_array(vec["vx"])
    vy = _col_to_float_array(vec["vy"])
    vz = _col_to_float_array(vec["vz"])

    r_km = np.vstack([x, y, z]).T * AU_KM
    v_km_s = np.vstack([vx, vy, vz]).T * AU_KM / DAY_S
    return jd, r_km, v_km_s

def horizons_now_vectors(body_id, now_dt, refplane=REFPLANE, aberr=ABERR):
    jd_epoch = utc_to_jd(now_dt)
    obj = Horizons(id=str(body_id), location="500@399", epochs=jd_epoch)
    vec = obj.vectors(refplane=refplane, aberrations=aberr)

    jd_now = float(_col_to_float_array(vec["datetime_jd"])[0])
    r_now = np.array([
        _col_to_float_array(vec["x"])[0],
        _col_to_float_array(vec["y"])[0],
        _col_to_float_array(vec["z"])[0],
    ]) * AU_KM
    v_now = np.array([
        _col_to_float_array(vec["vx"])[0],
        _col_to_float_array(vec["vy"])[0],
        _col_to_float_array(vec["vz"])[0],
    ]) * AU_KM / DAY_S
    return jd_now, r_now, v_now

# ----------------------------
# Orbital math
# ----------------------------
def eccentricity_vector(r_km, v_km_s):
    R = np.linalg.norm(r_km, axis=1)
    h = np.cross(r_km, v_km_s)
    evec = (np.cross(v_km_s, h) / MU_EARTH) - (r_km / R[:, None])
    return evec

def parabola_vertex_offset(y_m1, y0, y_p1):
    denom = (y_m1 - 2*y0 + y_p1)
    if denom == 0:
        return 0.0
    return 0.5 * (y_m1 - y_p1) / denom

def find_local_minima(arr):
    mid = arr[1:-1]
    mins = (mid < arr[:-2]) & (mid < arr[2:])
    return (np.where(mins)[0] + 1).astype(int)

def theil_sen_slope(t, y, max_points=TS_MAX_POINTS, max_pairs=TS_MAX_PAIRS, rng_seed=42):
    n = len(t)
    if n < 3:
        raise ValueError("Need >= 3 points")

    if n > max_points:
        idx = np.linspace(0, n - 1, max_points).round().astype(int)
        t = t[idx]
        y = y[idx]
        n = len(t)

    total_pairs = n * (n - 1) // 2
    rng = np.random.default_rng(rng_seed)

    if total_pairs <= max_pairs:
        slopes = []
        for i in range(n - 1):
            dt = t[i+1:] - t[i]
            dy = y[i+1:] - y[i]
            slopes.append(dy / dt)
        slopes = np.concatenate(slopes)
    else:
        slopes = np.empty(max_pairs, dtype=float)
        for k in range(max_pairs):
            i = rng.integers(0, n - 1)
            j = rng.integers(i + 1, n)
            slopes[k] = (y[j] - y[i]) / (t[j] - t[i])

    return float(np.median(slopes))

def ecliptic_longitude(r_km):
    # Since we requested refplane='ecliptic', x-y plane is ecliptic plane
    return np.arctan2(r_km[:, 1], r_km[:, 0])  # radians

def delta_lambda_deg(lam_moon_rad, lam_sun_rad):
    d = np.degrees(wrap_to_pi(lam_moon_rad - lam_sun_rad))  # (-180, +180]
    return d

# ----------------------------
# Full moon: find event times by opposition in ecliptic longitude
# ----------------------------
def refine_full_moon_opposition(jd0, half_window_days=REFINE_HALF_WINDOW_DAYS, step_min=REFINE_STEP_MIN):
    start_dt = jd_to_utc_datetime(jd0 - half_window_days)
    stop_dt  = jd_to_utc_datetime(jd0 + half_window_days)
    step = f"{int(step_min)} m"

    jd_m, r_m, _ = horizons_vectors(301, start_dt.strftime("%Y-%m-%d"), stop_dt.strftime("%Y-%m-%d"), step)
    jd_s, r_s, _ = horizons_vectors(10,  start_dt.strftime("%Y-%m-%d"), stop_dt.strftime("%Y-%m-%d"), step)
    n = min(len(jd_m), len(jd_s))
    jd_m, r_m, r_s = jd_m[:n], r_m[:n], r_s[:n]

    lam_m = ecliptic_longitude(r_m)
    lam_s = ecliptic_longitude(r_s)
    ddeg = delta_lambda_deg(lam_m, lam_s)  # (-180, +180]

    # opposition is at -180 (equivalent to +180)
    # minimize |wrap(ddeg + 180)|, where wrap gives (-180, +180]
    err = np.abs(wrap_deg_180(ddeg + 180.0))
    k = int(np.argmin(err))
    return float(jd_m[k]), float(err[k])

def find_full_moon_events_and_mask(jd, r_moon_km, r_sun_km, fullness_deg=FULLNESS_DEG):
    lam_m = ecliptic_longitude(r_moon_km)
    lam_s = ecliptic_longitude(r_sun_km)
    ddeg = delta_lambda_deg(lam_m, lam_s)  # (-180, +180]

    # Event detection: opposition => cos(Δλ) minimum (cos = -1 at 180°)
    # Use cos(dλ_rad) minima. Here dλ_rad = wrap_to_pi(lam_m - lam_s).
    dlam_rad = wrap_to_pi(lam_m - lam_s)
    cosdl = np.cos(dlam_rad)
    idx = find_local_minima(cosdl)

    cand = []
    for j in idx:
        # only keep candidates near opposition
        err = abs(wrap_deg_180(ddeg[j] + 180.0))  # degrees from exact opposition
        if err <= 30.0:
            cand.append(float(jd[j]))

    cand = np.array(sorted(cand), dtype=float)
    if len(cand) == 0:
        return np.array([], dtype=float), np.zeros_like(jd, dtype=bool)

    # de-dup candidates (full moons ~29.5d apart)
    keep = [cand[0]]
    for x in cand[1:]:
        if (x - keep[-1]) > 10.0:
            keep.append(x)
    cand = np.array(keep, dtype=float)

    # refine each candidate
    refined = []
    for jd0 in cand:
        jd_ref, err_deg = refine_full_moon_opposition(jd0)
        refined.append(jd_ref)

    refined = np.array(sorted(refined), dtype=float)

    # final de-dup
    keep = [refined[0]]
    for x in refined[1:]:
        if (x - keep[-1]) > 10.0:
            keep.append(x)
    refined = np.array(keep, dtype=float)

    # Realistic "duration" mask: within fullness_deg of exact opposition
    # Define error = |wrap(Δλ + 180)| in degrees (0 at full moon)
    err_series = np.abs(wrap_deg_180(ddeg + 180.0))
    mask_fullness = (err_series <= fullness_deg)

    return refined, mask_fullness

# ----------------------------
# 1) Fit apsidal cycle (perigees + e-vector secular fit)
# ----------------------------
fit_start_dt = parse_utc_date(FIT_START)
fit_stop_dt  = parse_utc_date(FIT_STOP)

per_jd = []
per_varpi = []

cur = fit_start_dt
last_jd = None
last_depth = None

while cur < fit_stop_dt:
    nxt = min(cur + timedelta(days=CHUNK_DAYS), fit_stop_dt)
    start = cur.strftime("%Y-%m-%d")
    stop  = nxt.strftime("%Y-%m-%d")

    jd, r_km, v_km_s = horizons_vectors(301, start, stop, FIND_STEP)
    dist = np.linalg.norm(r_km, axis=1)
    mins = find_local_minima(dist)

    if len(mins):
        step_days = float(np.median(np.diff(jd)))
        evec = eccentricity_vector(r_km, v_km_s)
        lon = np.arctan2(evec[:, 1], evec[:, 0])

        for j in mins:
            if not (0 < j < len(dist) - 1):
                continue
            off = parabola_vertex_offset(dist[j-1], dist[j], dist[j+1])
            jd_ref = float(jd[j] + off * step_days)
            depth = float(dist[j])

            if last_jd is not None and (jd_ref - last_jd) < MIN_SEP_DAYS:
                if depth < last_depth:
                    per_jd[-1] = jd_ref
                    per_varpi[-1] = float(lon[j])
                    last_jd = jd_ref
                    last_depth = depth
                continue

            per_jd.append(jd_ref)
            per_varpi.append(float(lon[j]))
            last_jd = jd_ref
            last_depth = depth

    cur = nxt

per_jd = np.array(per_jd, dtype=float)
per_varpi = np.array(per_varpi, dtype=float)

order = np.argsort(per_jd)
per_jd = per_jd[order]
per_varpi = per_varpi[order]
varpi_u = np.unwrap(per_varpi)

t0 = per_jd[0]
t_years = (per_jd - t0) / 365.25

# LS + Theil–Sen slope
b_ls, _ = np.polyfit(t_years, varpi_u, 1)
b_ts = theil_sen_slope(t_years, varpi_u)

b_ls_abs = abs(float(b_ls))
b_ts_abs = abs(float(b_ts))

ls_ok = (SLOPE_MIN < b_ls_abs < SLOPE_MAX)
ts_ok = (SLOPE_MIN < b_ts_abs < SLOPE_MAX)
agree = (ls_ok and ts_ok and (abs(b_ts_abs - b_ls_abs) / b_ls_abs <= AGREE_REL))

b = b_ts_abs if (ts_ok and agree) else b_ls_abs
slope_label = "Theil–Sen" if (ts_ok and agree) else "Least Squares"
a = float(np.median(varpi_u - b * t_years))
apsidal_period_years = (2*np.pi) / b

print("Secular apsidal rotation estimates (HORIZONS vectors, e-vector longitude):")
print(f"  LS slope b_ls  : {b_ls_abs:.6f} rad/yr -> period {(2*np.pi)/b_ls_abs:.6f} yr")
print(f"  Theil–Sen b_ts : {b_ts_abs:.6f} rad/yr -> period {(2*np.pi)/b_ts_abs:.6f} yr")
print(f"\nUsing slope: {slope_label}")
print(f"Adopted apsidal period: {apsidal_period_years:.6f} years")

# ----------------------------
# 2) Today + cycle bounds (phase 0 at varpi_fit = 0 mod 360)
# ----------------------------
now = datetime.now(timezone.utc)
jd_now, r_now, v_now = horizons_now_vectors(301, now)

dist_now = float(np.linalg.norm(r_now))
evec_now = eccentricity_vector(r_now.reshape(1,3), v_now.reshape(1,3))[0]
varpi_now = float(np.arctan2(evec_now[1], evec_now[0]))

t_now_years = (jd_now - t0) / 365.25
varpi_now_fit = b * t_now_years + a

k = np.floor(varpi_now_fit / (2*np.pi))
varpi_start = 2*np.pi * k

t_start_years = (varpi_start - a) / b
t_end_years   = (varpi_start + 2*np.pi - a) / b

jd_start = t0 + t_start_years * 365.25
jd_end   = t0 + t_end_years   * 365.25

cycle_len_years = (jd_end - jd_start) / 365.25
x_now_years = (jd_now - jd_start) / 365.25
phase_pct = 100.0 * (x_now_years / cycle_len_years)

print("\nToday (UTC):", now.isoformat())
print(f"Today distance (HORIZONS): {dist_now:,.0f} km")
print(f"Today in cycle: {x_now_years:.6f} years ({phase_pct:.2f}%)")
print("\nApprox cycle bounds (UTC-ish):")
print("  Start:", jd_to_utc_datetime(jd_start).isoformat())
print("  End  :", jd_to_utc_datetime(jd_end).isoformat())

# ----------------------------
# 3) Build plot data + full moon masks (chunked)
# ----------------------------
start_dt = jd_to_utc_datetime(jd_start)
end_dt   = jd_to_utc_datetime(jd_end)

x_list, d_list, jd_list = [], [], []
mask_list = []
fullmoon_events = []

cur = start_dt
while cur < end_dt:
    nxt = min(cur + timedelta(days=CHUNK_DAYS), end_dt)
    start = cur.strftime("%Y-%m-%d")
    stop  = nxt.strftime("%Y-%m-%d")

    jd_m, r_m, _ = horizons_vectors(301, start, stop, PLOT_STEP)
    jd_s, r_s, _ = horizons_vectors(10,  start, stop, PLOT_STEP)

    n = min(len(jd_m), len(jd_s))
    jd_m, r_m, r_s = jd_m[:n], r_m[:n], r_s[:n]

    dist = np.linalg.norm(r_m, axis=1)
    x_years = (jd_m - jd_start) / 365.25

    events, mask_full = find_full_moon_events_and_mask(jd_m, r_m, r_s, fullness_deg=FULLNESS_DEG)
    if len(events):
        fullmoon_events.extend(events.tolist())

    x_list.append(x_years)
    d_list.append(dist)
    jd_list.append(jd_m)
    mask_list.append(mask_full)

    cur = nxt

x_plot = np.concatenate(x_list) if x_list else np.array([])
d_plot = np.concatenate(d_list) if d_list else np.array([])
jd_plot = np.concatenate(jd_list) if jd_list else np.array([])
mask_fullness = np.concatenate(mask_list) if mask_list else np.array([], dtype=bool)
# ---------- DIAGNOSTIC: measure "full moon duration" in hours for each event ----------
plot_step_min = int(PLOT_STEP.split()[0])   # assumes PLOT_STEP like "60 m"
dt_hours = plot_step_min / 60.0

dur_hours = []
dist_at_event = []

for j in fullmoon_events:
    # focus on a local window around each event so we only count THIS full moon’s band
    local = np.abs(jd_plot - j) <= 2.0  # ±2 days is plenty
    dur_hours.append(float(np.sum(mask_fullness & local) * dt_hours))

    k = int(np.argmin(np.abs(jd_plot - j)))
    dist_at_event.append(float(d_plot[k]))

dur_hours = np.array(dur_hours)
dist_at_event = np.array(dist_at_event)

print("\nFull-moon band duration stats (hours):")
print("  min / median / max:", dur_hours.min(), np.median(dur_hours), dur_hours.max())

# Show the 10 shortest and 10 longest, with distance (apogee-ish is large distance)
ix_short = np.argsort(dur_hours)[:10]
ix_long  = np.argsort(dur_hours)[-10:][::-1]

print("\n10 shortest full-moon bands:")
for i in ix_short:
    print(" ", jd_to_utc_datetime(fullmoon_events[i]).isoformat(),
          f"dur={dur_hours[i]:.1f}h  dist≈{dist_at_event[i]:,.0f} km")

print("\n10 longest full-moon bands:")
for i in ix_long:
    print(" ", jd_to_utc_datetime(fullmoon_events[i]).isoformat(),
          f"dur={dur_hours[i]:.1f}h  dist≈{dist_at_event[i]:,.0f} km")

# Correlation: if negative, longer durations at larger distance (apogee) as physics suggests
if len(dur_hours) > 2:
    corr = np.corrcoef(dur_hours, dist_at_event)[0,1]
    print("\nCorrelation(duration, distance):", corr)

# de-dup full moon events across chunk edges
fullmoon_events = np.array(sorted(fullmoon_events), dtype=float)
if len(fullmoon_events):
    keep = [fullmoon_events[0]]
    for j in fullmoon_events[1:]:
        if (j - keep[-1]) > 10.0:
            keep.append(j)
    fullmoon_events = np.array(keep, dtype=float)

print(f"\nFull moon events found in cycle: {len(fullmoon_events)}")
print(f"Fullness criterion: |wrap(Δλ + 180°)| ≤ {FULLNESS_DEG:.1f}° (geocentric ecliptic longitude opposition)")
if len(fullmoon_events):
    print("First few full moon UTC-ish timestamps:")
    for j in fullmoon_events[:5]:
        print(" ", jd_to_utc_datetime(j).isoformat())

# ----------------------------
# 4) Dark mode plot + realistic full-moon duration overlay
# ----------------------------
plt.figure(figsize=(FIG_W, FIG_H), dpi=DPI, facecolor=COLOR_BG)
ax = plt.gca()
ax.set_facecolor(COLOR_BG)

# base distance curve
ax.plot(x_plot, d_plot, linewidth=0.7, color=COLOR_MOON, alpha=0.85)

# overlay only where "fullness" mask is true
d_full = d_plot.copy()
d_full[~mask_fullness] = np.nan
ax.plot(x_plot, d_full, linewidth=1.8, color=COLOR_FULL, alpha=0.95)

# today dot
ax.scatter([x_now_years], [dist_now], color=COLOR_DOT, s=90, zorder=10)

# cosmetics
ax.set_xlabel("Time within apsidal cycle (years) — HORIZONS vectors + robust e-vector secular fit", color=COLOR_FG)
ax.set_ylabel("Earth–Moon distance (km)", color=COLOR_FG)
ax.set_title(
    f"Earth–Moon distance over one apsidal cycle (period ≈ {apsidal_period_years:.3f} y), today marked\n"
    f"Yellow = geocentric full moon duration: |Δλ(opposition)| ≤ {FULLNESS_DEG:.1f}° (ecliptic longitude)",
    color=COLOR_FG
)

ax.tick_params(colors=COLOR_FG)
for spine in ax.spines.values():
    spine.set_color(COLOR_FG)

ax.set_xlim(0, cycle_len_years)

plt.savefig(
    "apsidal_fullmoon_dark.png",
    facecolor=COLOR_BG,
    bbox_inches="tight",
    pad_inches=0.15,
)
plt.show()