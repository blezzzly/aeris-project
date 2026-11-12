import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useAppStore } from '../stores/appStore';
import { useGeolocation } from '../hooks/useGeolocation';

type MapType = 'streets' | 'satellite';

const mapLayers: Record<MapType, { url: string; attribution: string }> = {
  streets: {
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap contributors',
  },
  satellite: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: '&copy; Esri',
  },
};

/**
 * Horizontal field of view of the MLX90640 camera module (BAA, wide band
 * variant) - the forward cone of the radar indicator is derived from it.
 */
const CAMERA_HFOV_DEG = 110;

/** Minimum movement (m) before a new course over ground is considered. */
const COURSE_MIN_MOVE_M = 2;

const EARTH_RADIUS_M = 6371000;

function haversineMeters(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Initial bearing (degrees clockwise from true north) from A to B. */
function bearingDegrees(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const y = Math.sin(toRad(lng2 - lng1)) * Math.cos(toRad(lat2));
  const x =
    Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
    Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(toRad(lng2 - lng1));
  return (Math.atan2(y, x) * 180) / Math.PI;
}

function angleDiff(a: number, b: number): number {
  let d = a - b;
  while (d > 180) d -= 360;
  while (d < -180) d += 360;
  return Math.abs(d);
}

const ARROW_SVG =
  '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">' +
  '<path d="M12 2.2 L19.2 20.4 L12 16.4 L4.8 20.4 Z" fill="#38bdf8" stroke="#ffffff" ' +
  'stroke-width="1.6" stroke-linejoin="round"/></svg>';

const PERSON_SVG =
  '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">' +
  '<circle cx="12" cy="7" r="3.6" fill="#ffffff"/>' +
  '<path d="M4.8 20.4c0-4 3.2-7.2 7.2-7.2s7.2 3.2 7.2 7.2z" fill="#ffffff"/></svg>';

export type MissionMapVariant = 'card' | 'page';

interface MissionMapProps {
  /** 'card' = dashboard card chrome, 'page' = full page (own header). */
  variant?: MissionMapVariant;
  /** Controlled tile type (used by the full Map page). */
  mapType?: MapType;
  onMapTypeChange?: (type: MapType) => void;
}

export function MissionMap({ variant = 'card', mapType: controlledType, onMapTypeChange }: MissionMapProps = {}) {
  const { uav, detections, setModalOpen, alertAcknowledged } = useAppStore();

  // ── Location permission ────────────────────────────────────────────────
  // The browser is asked once when the dashboard is opened; live positioning is
  // only initialised when the user allows it.
  const geo = useGeolocation();

  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstance = useRef<L.Map | null>(null);
  const tileLayer = useRef<L.TileLayer | null>(null);
  const uavMarker = useRef<L.Marker | null>(null);
  const detectionMarkers = useRef<Map<string, L.Marker>>(new Map());
  const initialized = useRef(false);
  const [internalMapType, setInternalMapType] = useState<MapType>('streets');
  const mapType = controlledType ?? internalMapType;
  const setMapType = (t: MapType) => {
    if (onMapTypeChange) onMapTypeChange(t);
    else setInternalMapType(t);
  };
  // Course over ground, derived from consecutive real positions.
  const courseRef = useRef<{ lat: number; lng: number; heading: number } | null>(null);

  // ── Real position resolution ───────────────────────────────────────────
  // Priority: the drone's own telemetry fix, then the browser's real fix.
  const droneFix =
    uav.positionLive && Number.isFinite(uav.lat) && Number.isFinite(uav.lng)
      ? { lat: uav.lat, lng: uav.lng, heading: uav.heading, source: 'telemetry' as const }
      : null;
  const browserFix =
    geo.granted && geo.fix
      ? {
          lat: geo.fix.lat,
          lng: geo.fix.lng,
          heading: geo.fix.heading,
          source: 'browser' as const,
        }
      : null;
  const position = droneFix ?? browserFix;

  // Heading: the real reported heading, otherwise the course over ground
  // derived from real successive positions. Never invented.
  const heading = useMemo(() => {
    if (!position) return null;
    if (position.heading != null && Number.isFinite(position.heading)) return position.heading;
    return courseRef.current?.heading ?? null;
  }, [position]);

  // Track the course over ground from real movement.
  useEffect(() => {
    if (!position) return;
    const prev = courseRef.current;
    if (!prev) {
      courseRef.current = { lat: position.lat, lng: position.lng, heading: 0 };
      return;
    }
    const moved = haversineMeters(prev.lat, prev.lng, position.lat, position.lng);
    if (moved >= COURSE_MIN_MOVE_M) {
      const b = bearingDegrees(prev.lat, prev.lng, position.lat, position.lng);
      courseRef.current = { lat: position.lat, lng: position.lng, heading: b };
    } else {
      courseRef.current = { ...prev, lat: position.lat, lng: position.lng };
    }
  }, [position?.lat, position?.lng, position?.source]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Detections that carry a REAL position ──────────────────────────────
  // Only 'gps' detections (reported by the drone's GPS) can be placed on the
  // map. Thermal-only detections have no position - they are not placed at a
  // made-up coordinate.
  const locatedDetections = useMemo(
    () =>
      detections.filter(
        (d) =>
          d.positionSource === 'gps' &&
          d.lat != null &&
          d.lng != null &&
          Number.isFinite(d.lat) &&
          Number.isFinite(d.lng) &&
          !(d.lat === 0 && d.lng === 0),
      ),
    [detections],
  );

  // Real detection range: the farthest real detection distance we know.
  // It is measured from the two real positions (great-circle separation) - the
  // distance a source reports for itself can refer to a different reference
  // and must not override the measured separation.
  const radarRangeM = useMemo(() => {
    if (!position || locatedDetections.length === 0) return null;
    let max = 0;
    for (const d of locatedDetections) {
      const geom = haversineMeters(position.lat, position.lng, d.lat as number, d.lng as number);
      if (geom > max) max = geom;
    }
    return max > 0 ? max : null;
  }, [locatedDetections, position?.lat, position?.lng]); // eslint-disable-line react-hooks/exhaustive-deps

  // Direction + range check for every located detection.
  const detectionState = useMemo(() => {
    return locatedDetections.map((d) => {
      const lat = d.lat as number;
      const lng = d.lng as number;
      if (!position) {
        return { detection: d, distance: null as number | null, bearing: null as number | null, inField: false, inRange: false };
      }
      const distance = haversineMeters(position.lat, position.lng, lat, lng);
      const bearing = bearingDegrees(position.lat, position.lng, lat, lng);
      const inField = heading != null && angleDiff(bearing, heading) <= CAMERA_HFOV_DEG / 2;
      // Tolerance: the radar range is derived from the real detections, so the
      // farthest one sits exactly on the limit - never exclude it over float
      // rounding.
      const inRange = radarRangeM == null ? true : distance <= radarRangeM * 1.02 + 1;
      return { detection: d, distance, bearing, inField, inRange };
    });
  }, [locatedDetections, position?.lat, position?.lng, heading, radarRangeM]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Map creation ───────────────────────────────────────────────────────
  useEffect(() => {
    if (!mapRef.current || initialized.current) return;

    const map = L.map(mapRef.current, { zoomControl: false }).setView(
      position ? [position.lat, position.lng] : [uav.lat, uav.lng],
      16,
    );

    tileLayer.current = L.tileLayer(mapLayers.streets.url, {
      attribution: mapLayers.streets.attribution,
    }).addTo(map);

    const uavIcon = L.divIcon({
      className: 'custom-leaflet-marker',
      html:
        '<div class="aeris-uav-marker">' +
        '<div class="aeris-radar">' +
        '<div class="aeris-radar-sweep"></div>' +
        '<div class="aeris-radar-cone" style="background:conic-gradient(from -55deg, rgba(56,189,248,0.26) 0deg 110deg, transparent 110deg)"></div>' +
        '</div>' +
        `<div class="aeris-uav-arrow">${ARROW_SVG}</div>` +
        '</div>',
      iconSize: [0, 0],
      iconAnchor: [0, 0],
    });
    uavMarker.current = L.marker(
      position ? [position.lat, position.lng] : [uav.lat, uav.lng],
      { icon: uavIcon, zIndexOffset: 700 },
    ).addTo(map);

    mapInstance.current = map;
    initialized.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (tileLayer.current && mapInstance.current) {
      tileLayer.current.remove();
      tileLayer.current = L.tileLayer(mapLayers[mapType].url, {
        attribution: mapLayers[mapType].attribution,
      }).addTo(mapInstance.current);
    }
  }, [mapType]);

  // ── Live UAV marker + radar orientation ────────────────────────────────
  useEffect(() => {
    const marker = uavMarker.current;
    const map = mapInstance.current;
    if (!marker || !map) return;

    if (position) {
      marker.setLatLng([position.lat, position.lng]);
      map.panTo([position.lat, position.lng], { animate: true, duration: 0.5 });
    }

    const el = marker.getElement();
    const arrow = el?.querySelector('.aeris-uav-arrow') as HTMLElement | null;
    const cone = el?.querySelector('.aeris-radar-cone') as HTMLElement | null;
    if (heading != null) {
      if (arrow) arrow.style.transform = `rotate(${heading}deg)`;
      if (cone) cone.style.transform = `rotate(${heading}deg)`;
    }
    // No real heading: the arrow points north and is dimmed, so it never
    // implies a direction that was not measured.
    if (arrow) {
      arrow.style.opacity = heading != null ? '1' : '0.45';
      arrow.style.filter = heading != null ? 'none' : 'grayscale(1)';
    }
  }, [position?.lat, position?.lng, heading]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Detection markers (real positions only) ─────────────────────────────
  useEffect(() => {
    const map = mapInstance.current;
    if (!map) return;
    const markers = detectionMarkers.current;

    const seen = new Set<string>();
    for (const item of detectionState) {
      const d = item.detection;
      seen.add(d.id);
      const lat = d.lat as number;
      const lng = d.lng as number;
      const emphasis = item.inField && item.inRange;
      const html =
        '<div class="aeris-detection-marker' +
        (emphasis ? ' aeris-detection-in-field' : '') +
        '" data-in-field="' + String(item.inField) +
        '" data-in-range="' + String(item.inRange) +
        '" data-bearing="' + (item.bearing == null ? '' : Math.round(item.bearing)) +
        '" data-distance-m="' + (item.distance == null ? '' : Math.round(item.distance)) +
        '" data-reported-m="' + (typeof item.detection.distance === 'number' ? Math.round(item.detection.distance) : '') +
        '">' +
        (emphasis ? '<div class="aeris-detection-ping"></div>' : '') +
        `<div class="aeris-detection-dot">${PERSON_SVG}</div>` +
        '</div>';

      let marker = markers.get(d.id);
      if (!marker) {
        const icon = L.divIcon({
          className: 'custom-leaflet-marker',
          html,
          iconSize: [0, 0],
          iconAnchor: [0, 0],
        });
        marker = L.marker([lat, lng], { icon, zIndexOffset: 500 }).addTo(map);
        marker.on('click', () => setModalOpen(true));
        markers.set(d.id, marker);
      } else {
        marker.setLatLng([lat, lng]);
        const icon = L.divIcon({
          className: 'custom-leaflet-marker',
          html,
          iconSize: [0, 0],
          iconAnchor: [0, 0],
        });
        marker.setIcon(icon);
      }
    }

    // Remove markers for detections that are gone or no longer located.
    for (const [id, marker] of markers) {
      if (!seen.has(id)) {
        map.removeLayer(marker);
        markers.delete(id);
      }
    }
  }, [detectionState, setModalOpen]);

  useEffect(() => {
    if (!alertAcknowledged) return;
    for (const marker of detectionMarkers.current.values()) {
      const ping = marker.getElement()?.querySelector('.aeris-detection-ping');
      ping?.remove();
    }
  }, [alertAcknowledged]);

  const mapTypes: { id: MapType; label: string }[] = [
    { id: 'streets', label: 'Streets' },
    { id: 'satellite', label: 'Satellite' },
  ];

  const positionSourceLabel =
    position?.source === 'telemetry'
      ? 'Drone telemetry GPS'
      : position?.source === 'browser'
        ? 'This device (browser GPS)'
        : 'No position';

  return (
    <div
      className={
        variant === 'card'
          ? 'lg:col-span-5 panel-card p-0 relative min-h-[300px] lg:min-h-0'
          : 'absolute inset-0'
      }
    >
      {variant === 'card' && (
        <div className="absolute top-4 left-4 z-10 bg-white/90 backdrop-blur-sm px-3 py-1.5 rounded-md shadow-sm border border-slate-200 flex items-center gap-2">
          <h2 className="text-sm font-bold text-slate-800">MISSION MAP</h2>
          <div className="flex items-center gap-1 ml-2 bg-slate-100 rounded-md p-0.5">
            {mapTypes.map((type) => (
              <button
                key={type.id}
                onClick={() => setMapType(type.id)}
                className={`px-2 py-0.5 text-[9px] font-medium rounded transition-colors ${
                  mapType === type.id
                    ? 'bg-white text-slate-800 shadow-sm'
                    : 'text-slate-500 hover:text-slate-700'
                }`}
              >
                {type.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div ref={mapRef} className="absolute inset-0 z-0 rounded-xl overflow-hidden" />

      <div className="hidden sm:flex absolute bottom-4 right-4 bg-white/90 backdrop-blur-sm p-3 rounded-lg border border-slate-200 shadow-sm text-[10px] text-slate-600 flex-col gap-2 z-10 pointer-events-none">
        <div className="flex items-center gap-2">
          <div className="w-4 h-4 flex items-center justify-center text-aeris">
            <i className="ph-fill ph-navigation-arrow text-[12px]" />
          </div>
          UAV Position ({positionSourceLabel})
        </div>
        <div className="flex items-center gap-2">
          <div className="w-4 h-4 rounded-full border border-dashed border-slate-400" />
          360&deg; Detection Field
        </div>
        <div className="flex items-center gap-2">
          <div className="w-4 h-4 bg-danger rounded-full flex items-center justify-center text-white">
            <i className="ph-fill ph-person text-[10px]" />
          </div>
          Survivor Detected
        </div>
      </div>
    </div>
  );
}
