// d3-geo treats polygon rings on the sphere: an exterior ring must wind
// clockwise (the opposite of RFC 7946), otherwise d3 draws the complement
// (the whole globe minus the polygon). Rewind for rendering only.

function ringArea(r: number[][]): number {
  let a = 0;
  for (let i = 0, n = r.length; i < n; i++) {
    const [x1, y1] = r[i];
    const [x2, y2] = r[(i + 1) % n];
    a += x1 * y2 - x2 * y1;
  }
  return a / 2;
}

function fixRings(rings: number[][][]): number[][][] {
  return rings.map((r, i) => {
    const a = ringArea(r);
    const wantNegative = i === 0; // exterior clockwise for d3, holes counter-clockwise
    return (wantNegative ? a > 0 : a < 0) ? [...r].reverse() : r;
  });
}

export function rewindForD3(g: any): any {
  if (!g) return g;
  if (g.type === "Feature") return { ...g, geometry: rewindForD3(g.geometry) };
  if (g.type === "FeatureCollection") return { ...g, features: g.features.map(rewindForD3) };
  if (g.type === "Polygon") return { ...g, coordinates: fixRings(g.coordinates) };
  if (g.type === "MultiPolygon") return { ...g, coordinates: g.coordinates.map(fixRings) };
  return g;
}

// ---------------------------------------------------------------- point + radius
export const RADIUS_KM_MIN = 1;
export const RADIUS_KM_MAX = 500;
const EARTH_KM = 6371.0088; // mean Earth radius (IUGG)

/** Parse "lat, lon" or separate fields; returns an error message or the values. */
export function checkPointRadius(lat: number, lon: number, km: number): string | null {
  if (![lat, lon, km].every(Number.isFinite)) return "Enter latitude, longitude and radius as numbers.";
  if (lat < -90 || lat > 90) return "Latitude must be between −90 and 90 (south is negative).";
  if (lon < -180 || lon > 180) return "Longitude must be between −180 and 180 (west is negative).";
  if (km < RADIUS_KM_MIN || km > RADIUS_KM_MAX) return `Radius must be between ${RADIUS_KM_MIN} and ${RADIUS_KM_MAX} km.`;
  // a circle that reaches a pole cannot be drawn as a simple lon/lat ring
  if (Math.abs(lat) + (km / EARTH_KM) * (180 / Math.PI) >= 89.5) return "That circle reaches a pole; choose a smaller radius or draw the area instead.";
  return null;
}

/** Geodesic circle (on a sphere) as a closed GeoJSON Polygon, counter-clockwise
 *  (RFC 7946). Longitudes stay continuous, so a circle crossing 180° has values
 *  beyond ±180; the server unwraps and splits such rings at the antimeridian. */
export function circlePolygon(lat: number, lon: number, km: number, n = 72): { type: "Polygon"; coordinates: number[][][] } {
  const r = Math.PI / 180;
  const phi1 = lat * r;
  const lam1 = lon * r;
  const d = km / EARTH_KM;
  const ring: number[][] = [];
  for (let i = 0; i < n; i++) {
    const theta = (-2 * Math.PI * i) / n; // negative bearing step: counter-clockwise
    const phi2 = Math.asin(Math.sin(phi1) * Math.cos(d) + Math.cos(phi1) * Math.sin(d) * Math.cos(theta));
    const lam2 = lam1 + Math.atan2(Math.sin(theta) * Math.sin(d) * Math.cos(phi1), Math.cos(d) - Math.sin(phi1) * Math.sin(phi2));
    ring.push([Math.round((lam2 / r) * 1e6) / 1e6, Math.round((phi2 / r) * 1e6) / 1e6]);
  }
  ring.push([...ring[0]]);
  return { type: "Polygon", coordinates: [ring] };
}

/** Great-circle distance in km (haversine). */
export function distanceKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = Math.PI / 180;
  const a = Math.sin(((lat2 - lat1) * r) / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lon2 - lon1) * r) / 2) ** 2;
  return 2 * EARTH_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}
