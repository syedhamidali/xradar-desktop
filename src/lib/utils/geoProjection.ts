/**
 * Geographic projection utilities for radar PPI display.
 *
 * Uses an azimuthal equidistant projection centered on the radar site,
 * which is appropriate for radar ranges up to ~300 km.
 */

const EARTH_RADIUS_M = 6371000;
const DEG2RAD = Math.PI / 180;
const RAD2DEG = 180 / Math.PI;

/**
 * Convert radar polar coordinates (range, azimuth) to geographic (lat, lon).
 *
 * @param range_m   - Slant range from radar in meters
 * @param azimuth_deg - Meteorological azimuth in degrees (0 = North, CW)
 * @param radarLat  - Radar site latitude
 * @param radarLon  - Radar site longitude
 * @returns [lat, lon] in degrees
 */
export function radarToLatLon(
  range_m: number,
  azimuth_deg: number,
  radarLat: number,
  radarLon: number,
): [number, number] {
  const angularDist = range_m / EARTH_RADIUS_M; // radians on sphere
  const bearing = azimuth_deg * DEG2RAD;
  const lat1 = radarLat * DEG2RAD;
  const lon1 = radarLon * DEG2RAD;

  const sinLat1 = Math.sin(lat1);
  const cosLat1 = Math.cos(lat1);
  const sinD = Math.sin(angularDist);
  const cosD = Math.cos(angularDist);

  const lat2 = Math.asin(sinLat1 * cosD + cosLat1 * sinD * Math.cos(bearing));
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(bearing) * sinD * cosLat1,
      cosD - sinLat1 * Math.sin(lat2),
    );

  return [lat2 * RAD2DEG, lon2 * RAD2DEG];
}

/**
 * Convert geographic (lat, lon) to screen pixel coordinates.
 *
 * Projects through the radar's data-space coordinate system (meters from
 * radar center) and then applies the same NDC transform the WebGL PPI uses.
 *
 * @returns [px, py] in CSS pixels relative to the canvas container
 */
export function latLonToPixel(
  lat: number,
  lon: number,
  radarLat: number,
  radarLon: number,
  maxRange: number,
  scale: number,
  translateX: number,
  translateY: number,
  width: number,
  height: number,
): [number, number] {
  // Geographic to data-space (meters from radar)
  const [dataX, dataY] = latLonToDataSpace(lat, lon, radarLat, radarLon);

  // Data-space to NDC (same transform as ppiRenderer)
  const aspect = width / height;
  const sx = (aspect >= 1 ? scale / aspect : scale) / maxRange;
  const sy = (aspect >= 1 ? scale : scale * aspect) / maxRange;
  const ndcX = (dataX + translateX) * sx;
  const ndcY = (dataY + translateY) * sy;

  // NDC [-1,1] to pixel
  const px = ((ndcX + 1) / 2) * width;
  const py = ((1 - ndcY) / 2) * height;
  return [px, py];
}

/**
 * Convert screen pixel to geographic (lat, lon).
 */
export function pixelToLatLon(
  px: number,
  py: number,
  radarLat: number,
  radarLon: number,
  maxRange: number,
  scale: number,
  translateX: number,
  translateY: number,
  width: number,
  height: number,
): [number, number] {
  // Pixel to NDC
  const ndcX = (px / width) * 2 - 1;
  const ndcY = 1 - (py / height) * 2;

  // NDC to data-space
  const aspect = width / height;
  const sx = (aspect >= 1 ? scale / aspect : scale) / maxRange;
  const sy = (aspect >= 1 ? scale : scale * aspect) / maxRange;
  const dataX = ndcX / sx - translateX;
  const dataY = ndcY / sy - translateY;

  // Data-space (meters) to lat/lon
  return dataSpaceToLatLon(dataX, dataY, radarLat, radarLon);
}

/**
 * Geographic to radar data-space (x = east meters, y = north meters).
 * Uses azimuthal equidistant approximation.
 */
export function latLonToDataSpace(
  lat: number,
  lon: number,
  radarLat: number,
  radarLon: number,
): [number, number] {
  const dLat = (lat - radarLat) * DEG2RAD;
  const dLon = (lon - radarLon) * DEG2RAD;
  const cosRadarLat = Math.cos(radarLat * DEG2RAD);

  // East-North in meters (small-angle approximation, fine for <300 km)
  const x = dLon * cosRadarLat * EARTH_RADIUS_M; // east = +x (sin(az) in data space)
  const y = dLat * EARTH_RADIUS_M;                // north = +y (cos(az) in data space)
  return [x, y];
}

/**
 * Radar data-space (meters) back to geographic.
 */
export function dataSpaceToLatLon(
  dataX: number,
  dataY: number,
  radarLat: number,
  radarLon: number,
): [number, number] {
  const cosRadarLat = Math.cos(radarLat * DEG2RAD);
  const lat = radarLat + (dataY / EARTH_RADIUS_M) * RAD2DEG;
  const lon = radarLon + (dataX / (EARTH_RADIUS_M * cosRadarLat)) * RAD2DEG;
  return [lat, lon];
}

/**
 * Web Mercator tile math utilities for slippy-map tile rendering.
 */

/** Convert lat/lon to fractional tile coordinates at a given zoom level. */
export function latLonToTile(
  lat: number,
  lon: number,
  zoom: number,
): { tx: number; ty: number } {
  const n = Math.pow(2, zoom);
  const tx = ((lon + 180) / 360) * n;
  const latRad = lat * DEG2RAD;
  const ty = ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n;
  return { tx, ty };
}

/** Convert fractional tile coordinates back to lat/lon. */
export function tileToLatLon(
  tx: number,
  ty: number,
  zoom: number,
): [number, number] {
  const n = Math.pow(2, zoom);
  const lon = (tx / n) * 360 - 180;
  const latRad = Math.atan(Math.sinh(Math.PI * (1 - (2 * ty) / n)));
  return [latRad * RAD2DEG, lon];
}

/**
 * Compute the appropriate tile zoom level for a given radar view.
 * Aims for a reasonable number of tiles covering the visible area.
 */
export function computeTileZoom(
  maxRange: number,
  scale: number,
  containerWidth: number,
): number {
  // Visible extent in meters (diameter at current zoom)
  const visibleExtent = (maxRange * 2) / scale;
  // Meters per pixel
  const metersPerPixel = visibleExtent / containerWidth;
  // At zoom z, one tile is ~40075016 / 2^z meters wide, covering 256 pixels
  // So meters per pixel at zoom z = 40075016 / (256 * 2^z)
  // Solve for z: 2^z = 40075016 / (256 * metersPerPixel)
  const z = Math.log2(40075016 / (256 * metersPerPixel));
  return Math.max(1, Math.min(18, Math.round(z)));
}
