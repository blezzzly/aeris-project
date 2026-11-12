import { useAppStore } from '../stores/appStore';
import { Icon, IconFill } from './Icon';
import { isValidDetectionFix } from '../types';

export function DetectionsTable() {
  const { detections, alertAcknowledged, setModalOpen, setActiveTab } = useAppStore();

  const fix = (lat: number | null, lng: number | null) =>
    isValidDetectionFix(lat, lng)
      ? `${(lat as number).toFixed(4)}° N, ${(lng as number).toFixed(4)}° E`
      : 'GPS unavailable (no fix)';

  return (
    <div className="lg:col-span-7 panel-card overflow-hidden flex flex-col">
      <div className="flex justify-between items-center mb-4 shrink-0">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-bold text-slate-800 uppercase tracking-wider">Detections Today</h3>
          {!alertAcknowledged && (
            <span className="bg-danger text-white text-xs px-2 py-0.5 rounded-full font-bold">1 New</span>
          )}
          {alertAcknowledged && (
            <span className="bg-green-100 text-green-700 text-xs px-2 py-0.5 rounded-full font-bold">All Clear</span>
          )}
        </div>
        <button
          onClick={() => setActiveTab('detections')}
          className="text-xs text-aeris font-semibold hover:text-aeris-dark flex items-center gap-1 transition-colors"
        >
          View All <Icon name="arrow-right" />
        </button>
      </div>

      <div className="flex-1 overflow-auto -mx-5 px-5">
        <table className="w-full text-left text-xs whitespace-nowrap">
          <thead className="text-[10px] text-slate-400 uppercase tracking-wider sticky top-0 bg-white z-10 shadow-sm">
            <tr>
              <th className="py-3 px-4 font-semibold">Images</th>
              <th className="py-3 px-4 font-semibold">Time</th>
              <th className="py-3 px-4 font-semibold">Classification</th>
              <th className="py-3 px-4 font-semibold hidden md:table-cell">Location (GPS)</th>
              <th className="py-3 px-4 font-semibold hidden sm:table-cell">Distance</th>
              <th className="py-3 px-4 font-semibold hidden md:table-cell">Confidence</th>
              <th className="py-3 px-4 font-semibold text-right">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 text-slate-700">
            {detections.map((detection) => (
              <tr
                key={detection.id}
                className={`hover:bg-slate-50 transition-colors cursor-pointer ${
                  detection.status === 'new' && !alertAcknowledged ? 'bg-red-50' : ''
                }`}
                onClick={() => setModalOpen(true)}
              >
                <td className="py-3 px-4">
                  <div className="flex gap-2">
                    <div className="w-10 h-10 bg-slate-200 rounded overflow-hidden relative">
                      <img
                        src="/image/placeholder.svg"
                        className="w-full h-full object-cover"
                        alt="RGB thumbnail"
                      />
                      {detection.status === 'new' && !alertAcknowledged && (
                        <div className="absolute inset-2 border border-danger" />
                      )}
                    </div>
                    <div className="w-10 h-10 bg-indigo-900 rounded overflow-hidden relative hidden sm:block">
                      <img
                        src="/image/placeholder.svg"
                        className="w-full h-full object-cover filter contrast-150 hue-rotate-180 saturate-200"
                        alt="Thermal thumbnail"
                      />
                    </div>
                  </div>
                </td>
                <td className="py-3 px-4 font-medium">{detection.timestamp}</td>
                <td className="py-3 px-4 text-danger font-medium">
                  <IconFill name="person" className="inline mr-1" /> Survivor
                </td>
                <td className="py-3 px-4 font-mono text-slate-500 hidden md:table-cell">
                  {fix(detection.lat, detection.lng)}
                </td>
                <td className="py-3 px-4 hidden sm:table-cell">
                  {detection.distance != null ? `${detection.distance} m` : 'N/A'}
                </td>
                <td className="py-3 px-4 hidden md:table-cell">
                  <div className="flex items-center gap-2">
                    <span>{detection.confidence}%</span>
                    <div className="w-12 h-1.5 bg-slate-200 rounded-full">
                      <div className="h-1.5 bg-aeris rounded-full" style={{ width: `${detection.confidence}%` }} />
                    </div>
                  </div>
                </td>
                <td className="py-3 px-4 text-right">
                  {detection.status === 'new' && !alertAcknowledged ? (
                    <span className="inline-block px-2.5 py-1 bg-danger-light text-danger border border-danger/20 rounded font-bold text-[10px] uppercase tracking-wide animate-pulse">
                      New
                    </span>
                  ) : (
                    <span className="inline-block px-2.5 py-1 bg-green-50 text-green-700 border border-green-200 rounded font-bold text-[10px] uppercase tracking-wide">
                      Ack'd
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
