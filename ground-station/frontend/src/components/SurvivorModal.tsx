import { useAppStore } from '../stores/appStore';
import { Icon, IconFill } from './Icon';
import { isValidDetectionFix } from '../types';

export function SurvivorModal() {
  const { modalOpen, setModalOpen, acknowledgeAlert, alertAcknowledged, detections, thermal } = useAppStore();

  if (!modalOpen) return null;

  const currentDetection = detections.find((d) => d.status === 'new') || detections[0];
  const hasFix = isValidDetectionFix(currentDetection?.lat, currentDetection?.lng);

  const coord = (v: number | null | undefined) =>
    typeof v === 'number' && Number.isFinite(v) ? `${v.toFixed(4)}` : 'N/A';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      onClick={() => setModalOpen(false)}
    >
      <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm" />

      <div
        className="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[90vh] flex flex-col relative z-10 border border-slate-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-4 sm:px-6 py-4 border-b border-slate-100 flex justify-between items-start shrink-0">
          <div className="flex items-center gap-3 text-danger">
            <IconFill name="warning-circle" className="text-2xl sm:text-3xl" />
            <div>
              <h2 className="text-lg sm:text-xl font-bold uppercase tracking-wide">Survivor Detected</h2>
              <div className="text-[10px] text-slate-500 font-mono">Alert ID: {currentDetection?.id}</div>
            </div>
          </div>
          <button
            onClick={() => setModalOpen(false)}
            className="text-slate-400 hover:text-slate-600 transition-colors p-1 rounded"
          >
            <Icon name="x" className="text-xl" />
          </button>
        </div>

        <div className="p-4 sm:p-6 overflow-y-auto flex-1">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
            <div>
              <h3 className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Visual (RGB)</h3>
              <div className="bg-slate-100 rounded-xl aspect-video md:aspect-video overflow-hidden border border-slate-200 relative">
                <img
                  src="/image/placeholder.svg"
                  className="w-full h-full object-cover"
                  alt="RGB High Res"
                />
                <div className="absolute top-1/4 left-1/4 right-1/4 bottom-1/4 border-2 border-danger opacity-80 rounded pointer-events-none" />
              </div>
            </div>

            <div className="bg-slate-50 rounded-xl p-4 sm:p-5 border border-slate-200 flex flex-col">
              <h3 className="text-[10px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1 mb-4">
                <IconFill name="map-pin" /> Location Data
              </h3>

              <div className="space-y-4 flex-1">
                <div>
                  <div className="text-[10px] text-slate-400 uppercase">GPS Coordinates</div>
                  {hasFix ? (
                    <>
                      <div className="font-mono text-sm font-semibold text-slate-800">
                        {coord(currentDetection?.lat)}° N, {coord(currentDetection?.lng)}° E
                      </div>
                      <div className="text-[10px] text-slate-500">Real GPS fix</div>
                    </>
                  ) : (
                    <>
                      <div className="font-mono text-sm font-semibold text-slate-500">
                        GPS unavailable (no fix)
                      </div>
                      <div className="text-[10px] text-slate-500">Detection source reports no position — coordinates are not estimated</div>
                    </>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <div className="text-[10px] text-slate-400 uppercase">Altitude</div>
                    <div className="text-sm font-semibold text-slate-800">
                      {currentDetection?.altitude != null ? `${currentDetection.altitude} m AMSL` : 'N/A'}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-400 uppercase">Dist from UAV</div>
                    <div className="text-sm font-semibold text-slate-800">
                      {currentDetection?.distance != null ? `${currentDetection.distance} m` : 'N/A'}
                    </div>
                  </div>
                </div>

                <div>
                  <div className="text-[10px] text-slate-400 uppercase">Time Detected</div>
                  <div className="text-sm font-semibold text-slate-800">{currentDetection?.timestamp}</div>
                </div>
              </div>

              <button
                onClick={() => setModalOpen(false)}
                className="w-full mt-4 py-2 bg-white border border-aeris text-aeris font-semibold rounded-lg hover:bg-aeris-light transition-colors text-xs flex justify-center items-center gap-2"
              >
                <Icon name="map-trifold" /> View on Map
              </button>
            </div>
          </div>

          <div>
            <div className="flex flex-col sm:flex-row justify-between sm:items-center gap-2 mb-2">
              <h3 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-2">
                <Icon name="user" /> Detected Survivor(s)
              </h3>
              <span className="text-xs text-slate-500">Total Detected: {detections.length}</span>
            </div>

            <div className="border border-slate-200 rounded-lg overflow-x-auto">
              <table className="w-full text-left text-xs whitespace-nowrap min-w-[600px]">
                <thead className="bg-slate-50 text-[10px] text-slate-500 uppercase">
                  <tr>
                    <th className="py-2 px-3">ID</th>
                    <th className="py-2 px-3 text-center">Thumbnail</th>
                    <th className="py-2 px-3">Heat Range</th>
                    <th className="py-2 px-3">Status</th>
                    <th className="py-2 px-3">AI Confidence</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-slate-100">
                  <tr>
                    <td className="py-3 px-3 font-medium text-slate-700">1</td>
                    <td className="py-3 px-3 text-center">
                      <img
                        src="/image/placeholder.svg"
                        className="w-8 h-8 object-cover rounded inline-block"
                        alt="RGB thumbnail"
                      />
                    </td>
                    <td className="py-3 px-3">
                      <div className="font-semibold text-slate-800">
                        {thermal?.ok && thermal.min_c != null && thermal.max_c != null
                          ? `${thermal.min_c.toFixed(1)}°C – ${thermal.max_c.toFixed(1)}°C`
                          : 'N/A — no thermal frame'}
                      </div>
                      <div className="flex h-1 bg-gradient-to-r from-blue-500 via-yellow-500 to-red-500 rounded mt-1 opacity-70" />
                    </td>
                    <td className="py-3 px-3">
                      <span className="text-green-600 font-bold flex items-center gap-1">
                        <span className="w-1.5 h-1.5 bg-green-500 rounded-full" /> DETECTED
                      </span>
                      <div className="text-[10px] text-slate-500 ml-2">thermal source</div>
                    </td>
                    <td className="py-3 px-3">
                      <div className="font-bold text-aeris text-sm mb-1">{Math.round(currentDetection?.confidence ?? 0)}%</div>
                      <div className="w-full bg-slate-200 h-1.5 rounded-full">
                        <div className="bg-aeris h-1.5 rounded-full" style={{ width: `${Math.min(100, Math.max(0, currentDetection?.confidence ?? 0))}%` }} />
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-[10px] text-slate-400 mt-2 italic">
              <Icon name="info" className="inline mr-1" /> Ambient heat range from the MLX90640 @0x33 (I2C). Detection coordinates are shown only when a real GPS fix exists.
            </p>
          </div>
        </div>

        <div className="p-4 sm:p-6 border-t border-slate-100 bg-slate-50 rounded-b-2xl flex flex-col sm:flex-row gap-3 sm:gap-4 shrink-0">
          <button
            onClick={() => {
              setModalOpen(false);
              acknowledgeAlert();
            }}
            className={`flex-1 text-white font-semibold py-3 rounded-lg shadow-sm transition-colors flex items-center justify-center gap-2 ${
              alertAcknowledged ? 'bg-slate-400 cursor-not-allowed' : 'bg-aeris hover:bg-aeris-dark'
            }`}
            disabled={alertAcknowledged}
          >
            {alertAcknowledged ? (
              <Icon name="check-circle" className="text-lg" />
            ) : (
              <Icon name="check" className="text-lg" />
            )}
            <span>{alertAcknowledged ? 'Acknowledged' : 'Acknowledge Alert'}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
