# -*- coding: utf-8 -*-
"""
argo_floats_animation.py

Animated world map of the Argo float network, from the first profile to today.

Everything comes from the profile index files of the Argo Global Data Assembly
Center (date, latitude and longitude of every profile)

    MODE = 'ALL' : all Argo floats, core and BGC (ar_index_global_prof.txt)
    MODE = 'BGC' : BGC floats only (argo_bio-profile_index.txt), 
                   optionally restricted to the profiles measuring given parameters

How to use it: edit section 1, then run the script. The index file is downloaded
from the Ifremer FTP server on the first run (130 to 320 MB), then only when a
newer version is available.

Requirements:
    numpy pandas matplotlib cartopy tqdm imageio-ffmpeg
    (imageio-ffmpeg provides ffmpeg for the MP4 output, a system ffmpeg also works)

"""

#%% Modules

import os
from datetime import datetime, timezone
from ftplib import FTP

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.animation import FFMpegWriter, PillowWriter
from matplotlib.projections import PolarAxes
from mpl_toolkits.axes_grid1.inset_locator import inset_axes
from tqdm import tqdm


#%% 1. Parameters

# --- Floats to show ---
MODE = 'ALL'                # 'ALL' (all Argo floats) or 'BGC' (BGC floats)
BGC_PARAMETERS = []         # MODE = 'BGC' only: parameters a profile must all have, exact names,
                            # e.g. ['CHLA', 'BBP700']. [] = all BGC floats, including DOXY-only ones

# --- Input and output paths (relative paths start from the working directory) ---
INDEX_DIR = 'argo_index'    # folder of the index files
OUTPUT_DIR = 'output'       # folder of the videos
OUTPUT_NAME = None          # file name without extension, None = built from MODE and BGC_PARAMETERS

# --- Index files on the Ifremer FTP server ---
DOWNLOAD_INDEX = True       # False = use the local index file as it is
FTP_HOST = 'ftp.ifremer.fr'
FTP_DIR = 'ifremer/argo'
INDEX_FILES = {'ALL': 'ar_index_global_prof.txt', 'BGC': 'argo_bio-profile_index.txt'}

# --- Video ---
OUTPUT_FORMATS = ['mp4']    # 'mp4' and/or 'gif'. 
FPS = 25                    # frames per second
STEP_DAYS_START = 20        # days per frame at the beginning of the record
STEP_DAYS_END = 20          # days per frame at the end of the record. 
                            # Lower than STEP_DAYS_START = the animation slows down
HOLD_LAST_S = 3             # pause on the last image, in seconds
FIGURE_SIZE = (15, 8)       # inches
MP4_DPI = 100               # with FIGURE_SIZE: 1500 x 800 pixels
MP4_CRF = 18                # H.264 quality, lower = better and heavier (18 = visually clean)
GIF_DPI = 60                # GIF files grow fast: 56 MB at 60 dpi for MODE = 'ALL' with steps of 45 to 7 days

# --- Map and counters ---
TRAIL_DAYS = 90             # a profile fades out and disappears after this number of days
ACTIVE_DAYS = 30            # active float = at least one profile during the last ACTIVE_DAYS days
POINT_SIZES = {'ALL': (0.3, 3), 'BGC': (1, 6)}  # (oldest, newest) profile, by MODE
POINT_COLOR = 'white'
OCEAN_COLOR = '#002649'
LAND_COLOR = '#BAD4E2'
TEXT_COLOR = '#486270'      # also the color of the deployed floats and of the year ring
ACTIVE_COLOR = '#F7C1A8'    # active floats

# --- Quick look ---
PREVIEW_DATE = None         # e.g. '2024-06-01': saves one PNG of this date instead of the video


#%% 2. Download the index file

if MODE not in INDEX_FILES:
    raise ValueError(f"MODE must be one of {list(INDEX_FILES)}, not {MODE!r}")

index_name = INDEX_FILES[MODE]
index_path = os.path.join(INDEX_DIR, index_name)
os.makedirs(INDEX_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


def download_index(name, local_path):
    """Downloads name from the FTP server if it is absent locally or newer on the server."""
    ftp = FTP(FTP_HOST, timeout=120)
    try:
        ftp.login()
        ftp.set_pasv(True)
        ftp.cwd(FTP_DIR)
        # MDTM gives the UTC modification time of the remote file
        remote_time = datetime.strptime(ftp.sendcmd(f'MDTM {name}')[4:].strip(), '%Y%m%d%H%M%S')
        remote_time = remote_time.replace(tzinfo=timezone.utc)
        if os.path.exists(local_path):
            local_time = datetime.fromtimestamp(os.path.getmtime(local_path), tz=timezone.utc)
            if local_time >= remote_time:
                print(f"{name} is up to date")
                return

        ftp.voidcmd('TYPE I')
        size = ftp.size(name)
        # A temporary file, so that an interrupted download never looks like an up-to-date index
        temp_path = local_path + '.part'
        with open(temp_path, 'wb') as f, tqdm(total=size, unit='B', unit_scale=True, desc=name) as bar:
            def write_chunk(chunk):
                f.write(chunk)
                bar.update(len(chunk))
            ftp.retrbinary(f'RETR {name}', write_chunk)
        os.replace(temp_path, local_path)
    finally:
        ftp.close()


if DOWNLOAD_INDEX:
    try:
        download_index(index_name, index_path)
    except Exception as e:
        if not os.path.exists(index_path):
            raise
        print(f"Download failed ({e}), using the local copy of {index_name}")


#%% 3. Read and clean the index

columns = ['file', 'date', 'latitude', 'longitude'] + (['parameters'] if MODE == 'BGC' else [])
df_all = pd.read_csv(index_path, comment='#', usecols=columns, dtype={'date': str})

with open(index_path, encoding='utf-8') as f:
    index_date = next(line for line in f if line.startswith('# Date of update')).split(':')[1].strip()
print(f"Index: {index_path} (updated on {index_date[:8]})")

if MODE == 'BGC' and len(BGC_PARAMETERS) > 0:
    # Exact names: BBP700 must not match BBP700_2
    parameter_lists = df_all['parameters'].fillna('').str.split()
    df_all = df_all[parameter_lists.apply(lambda p: all(x in p for x in BGC_PARAMETERS))]

df_all['WMO'] = df_all['file'].str.split('/').str[1]
df_all['date'] = pd.to_datetime(df_all['date'], format='%Y%m%d%H%M%S', errors='coerce')
df_all = df_all.dropna(subset=['date', 'latitude', 'longitude'])
# Fill values (-99.999, -999.999) and longitudes given in 0-360
df_all = df_all[(df_all['latitude'].abs() <= 90) & (df_all['longitude'] >= -180) & (df_all['longitude'] <= 360)]
df_all['longitude'] = ((df_all['longitude'] + 180) % 360) - 180
df_all = df_all.sort_values('date').reset_index(drop=True)

if df_all.empty:
    raise ValueError(f"No profile found with BGC_PARAMETERS = {BGC_PARAMETERS}")

print(f"{len(df_all)} profiles, {df_all['WMO'].nunique()} floats, "
      f"{df_all['date'].min():%Y-%m-%d} -> {df_all['date'].max():%Y-%m-%d}")

dates_all = df_all['date'].values
lons_all = df_all['longitude'].values
lats_all = df_all['latitude'].values
wmo_codes = pd.factorize(df_all['WMO'])[0]
first_dates = np.sort(df_all.groupby('WMO')['date'].min().values)  # first profile = deployment


#%% 4. Frame dates and float counts

# The step goes linearly from STEP_DAYS_START to STEP_DAYS_END over the record
start_date = df_all['date'].min().normalize()
end_date = df_all['date'].max()
total_days = (end_date - start_date).days
frame_dates = [start_date]
while frame_dates[-1] < end_date:
    frac = (frame_dates[-1] - start_date).days / total_days
    step = STEP_DAYS_START + (STEP_DAYS_END - STEP_DAYS_START) * frac
    frame_dates.append(frame_dates[-1] + pd.Timedelta(days=step))
frame_dates[-1] = end_date
n_anim = len(frame_dates)
frame_dates = frame_dates + [end_date] * int(HOLD_LAST_S * FPS)
print(f"{n_anim} animated frames + {len(frame_dates) - n_anim} of pause: "
      f"{len(frame_dates) / FPS:.1f} s at {FPS} fps")


def frame_counts(t):
    t64 = np.datetime64(t)
    n_deployed = np.searchsorted(first_dates, t64, side='right')
    i0 = np.searchsorted(dates_all, t64 - np.timedelta64(ACTIVE_DAYS, 'D'), side='left')
    i1 = np.searchsorted(dates_all, t64, side='right')
    return n_deployed, len(np.unique(wmo_codes[i0:i1]))


counts = np.array([frame_counts(t) for t in frame_dates[:n_anim]])
deployed_series, active_series = counts[:, 0], counts[:, 1]
x_dates = np.array(frame_dates[:n_anim], dtype='datetime64[ns]')


#%% 5. Map

MIN_SIZE, MAX_SIZE = POINT_SIZES[MODE]

fig = plt.figure(figsize=FIGURE_SIZE)
ax = plt.axes(projection=ccrs.Robinson())
ax.set_global()
ax.coastlines()
ax.add_feature(cfeature.LAND, facecolor=LAND_COLOR)
ax.add_feature(cfeature.OCEAN, facecolor=OCEAN_COLOR)
fig.subplots_adjust(left=0.01, right=0.99, bottom=0.05, top=0.97)

scatter = ax.scatter([], [], s=[], c=POINT_COLOR, linewidths=0, transform=ccrs.PlateCarree())

ax_counts = inset_axes(ax, width="13.5%", height="15%", loc='lower right', borderpad=0.7)
ax_year = inset_axes(ax, width="15%", height="15%", loc='upper right', borderpad=-0.9,
                     axes_class=PolarAxes)


def clean_axes(a):
    a.set_facecolor('none')
    a.set_xticks([])
    a.set_yticks([])
    for spine in a.spines.values():
        spine.set_visible(False)


def update(frame):
    i_anim = min(frame, n_anim - 1)
    current_date = frame_dates[frame]
    t64 = np.datetime64(current_date)

    # --- Profiles of the last TRAIL_DAYS days, size and opacity decreasing with age ---
    i0 = np.searchsorted(dates_all, t64 - np.timedelta64(TRAIL_DAYS, 'D'), side='left')
    i1 = np.searchsorted(dates_all, t64, side='right')
    age_days = (t64 - dates_all[i0:i1]) / np.timedelta64(1, 'D')
    alpha = np.clip(1 - age_days / TRAIL_DAYS, 0, 1)
    if i1 > i0:
        scatter.set_offsets(np.column_stack([lons_all[i0:i1], lats_all[i0:i1]]))
        scatter.set_sizes(MIN_SIZE + (MAX_SIZE - MIN_SIZE) * alpha)
        scatter.set_alpha(alpha)
    else:
        scatter.set_offsets(np.empty((0, 2)))

    # --- Deployed (cumulative) and active floats ---
    ax_counts.clear()
    ax_counts.set_xlim(x_dates[0], x_dates[-1])
    ax_counts.set_ylim(0, deployed_series[-1])
    ax_counts.fill_between(x_dates[:i_anim + 1], 0, deployed_series[:i_anim + 1], color=TEXT_COLOR, lw=0)
    ax_counts.fill_between(x_dates[:i_anim + 1], 0, active_series[:i_anim + 1], color=ACTIVE_COLOR, lw=0)
    clean_axes(ax_counts)
    ax_counts.text(0, -0.05, f"{deployed_series[i_anim]} Deployed floats\n{active_series[i_anim]} Active floats",
                   ha='left', va='top', fontsize=10, color=TEXT_COLOR, weight='bold',
                   transform=ax_counts.transAxes)

    # --- Year ring ---
    ax_year.clear()
    clean_axes(ax_year)
    ax_year.set_theta_direction(-1)
    ax_year.set_theta_zero_location('E')
    outer_radius, ring_width = 2, 0.6
    inner_radius = outer_radius - ring_width
    year = current_date.year
    year_fraction = (current_date - pd.Timestamp(year=year, month=1, day=1)).days / 365.0
    ax_year.bar(x=0, height=ring_width, width=year_fraction * 2 * np.pi, bottom=inner_radius,
                color=TEXT_COLOR, edgecolor='none', align='edge')
    ax_year.set_ylim(0, outer_radius)
    ax_year.text(0, 0, str(year), ha='center', va='center',
                 fontsize=14, fontweight='bold', color=TEXT_COLOR)


#%% 6. Render

if OUTPUT_NAME is None:
    OUTPUT_NAME = 'Argo_floats_ALL' if MODE == 'ALL' else '_'.join(['Argo_floats_BGC'] + BGC_PARAMETERS)
output_base = os.path.join(OUTPUT_DIR, OUTPUT_NAME)

if PREVIEW_DATE is not None:
    i_preview = int(np.argmin(np.abs(x_dates - np.datetime64(pd.Timestamp(PREVIEW_DATE)))))
    update(i_preview)
    fig.savefig(f"{output_base}_preview_{PREVIEW_DATE}.png", dpi=MP4_DPI)
    print(f"Preview saved in {OUTPUT_DIR}")
else:
    if 'mp4' in OUTPUT_FORMATS:
        try:
            import imageio_ffmpeg
            mpl.rcParams['animation.ffmpeg_path'] = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            if not FFMpegWriter.isAvailable():
                raise RuntimeError("The MP4 output needs ffmpeg: pip install imageio-ffmpeg")

    writers = {'mp4': (FFMpegWriter(fps=FPS, codec='h264', extra_args=['-crf', str(MP4_CRF)]), MP4_DPI),
               'gif': (PillowWriter(fps=FPS), GIF_DPI)}
    for fmt in OUTPUT_FORMATS:
        writer, dpi = writers[fmt]
        out = f"{output_base}.{fmt}"
        with writer.saving(fig, out, dpi=dpi):
            for frame in tqdm(range(len(frame_dates)), desc=f"{fmt.upper()} rendering", unit='frame'):
                update(frame)
                writer.grab_frame()
        print(f"{out} ({os.path.getsize(out) / 1e6:.0f} MB)")
