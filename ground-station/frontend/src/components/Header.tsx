import { useState, useRef, useEffect } from 'react';
import { useAppStore } from '../stores/appStore';
import { Icon, IconFill } from './Icon';
import { RpiSystemHealth } from './RpiSystemHealth';
import { isValidDetectionFix } from '../types';

export function Header() {
  const { toggleSidebar, setActiveTab, detections } = useAppStore();
  const [profileOpen, setProfileOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const notifRef = useRef<HTMLDivElement>(null);


  const coord = (lat: number | null | undefined, lng: number | null | undefined) =>
    isValidDetectionFix(lat, lng)
      ? `${(lat as number).toFixed(4)}° N, ${(lng as number).toFixed(4)}° E`
      : 'GPS unavailable (no fix)';

  const newDetections = detections.filter(d => d.status === 'new');
  const acknowledgedDetections = detections.filter(d => d.status === 'acknowledged');

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (notifRef.current && !notifRef.current.contains(event.target as Node)) {
        setNotifOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <header className="h-20 bg-white border-b border-slate-200 flex items-center justify-between px-4 lg:px-6 shrink-0 z-50">
      <div className="flex items-center shrink-0">
        <button
          className="lg:hidden p-2 text-slate-500 hover:bg-slate-50 rounded-lg"
          onClick={toggleSidebar}
        >
          <Icon name="list" className="text-2xl" />
        </button>
      </div>

      <RpiSystemHealth />

      <div className="flex items-center gap-3 lg:gap-5 shrink-0">
        <div className="relative" ref={notifRef}>
          <button
            className="relative text-slate-400 hover:text-slate-600 transition-colors p-2 rounded-lg"
            onClick={() => setNotifOpen(!notifOpen)}
          >
            <Icon name="bell" className="text-xl lg:text-2xl" />
            {newDetections.length > 0 && (
              <span className="absolute top-1.5 right-1.5 min-w-[16px] h-4 px-1 bg-danger text-white text-[9px] font-bold rounded-full border-2 border-white flex items-center justify-center">
                {newDetections.length}
              </span>
            )}
          </button>

          {notifOpen && (
            <div className="absolute top-full right-0 mt-3 w-80 bg-white rounded-lg shadow-lg border border-slate-200 z-[60] overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-100 flex justify-between items-center">
                <h3 className="font-bold text-sm text-slate-700">Notifications</h3>
                {newDetections.length > 0 && (
                  <span className="text-[10px] bg-danger text-white px-2 py-0.5 rounded-full font-bold">
                    {newDetections.length} New
                  </span>
                )}
              </div>

              <div className="max-h-96 overflow-y-auto">
                {newDetections.length === 0 && acknowledgedDetections.length === 0 ? (
                  <div className="px-4 py-8 text-center text-slate-400 text-sm">
                    <Icon name="bell" className="text-3xl mx-auto mb-2 opacity-50" />
                    <p>No notifications</p>
                  </div>
                ) : (
                  <>
                    {newDetections.map((d) => (
                      <button
                        key={d.id}
                        onClick={() => {
                          setActiveTab('detections');
                          setNotifOpen(false);
                        }}
                        className="w-full px-4 py-3 flex items-start gap-3 hover:bg-slate-50 border-b border-slate-100 text-left transition-colors"
                      >
                        <div className="w-8 h-8 rounded-full bg-red-100 text-danger flex items-center justify-center shrink-0">
                          <IconFill name="warning-circle" className="text-base" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-semibold text-slate-800">Survivor Detected</div>
                          <div className="text-xs text-slate-500 truncate">
                            {coord(d.lat, d.lng)}
                          </div>
                          <div className="text-[10px] text-slate-400 mt-0.5 flex items-center gap-2">
                            <span>{d.timestamp}</span>
                            <span className="text-aeris font-bold">{Math.round(d.confidence)}%</span>
                          </div>
                        </div>
                        <div className="w-2 h-2 rounded-full bg-danger shrink-0 mt-2 animate-pulse" />
                      </button>
                    ))}

                    {acknowledgedDetections.slice(0, 5).map((d) => (
                      <button
                        key={d.id}
                        onClick={() => {
                          setActiveTab('detections');
                          setNotifOpen(false);
                        }}
                        className="w-full px-4 py-3 flex items-start gap-3 hover:bg-slate-50 border-b border-slate-100 text-left transition-colors opacity-60"
                      >
                        <div className="w-8 h-8 rounded-full bg-green-100 text-aeris flex items-center justify-center shrink-0">
                          <IconFill name="check-circle" className="text-base" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-semibold text-slate-800">Survivor Acknowledged</div>
                          <div className="text-xs text-slate-500 truncate">
                            {coord(d.lat, d.lng)}
                          </div>
                          <div className="text-[10px] text-slate-400 mt-0.5">
                            {d.timestamp} · {Math.round(d.confidence)}%
                          </div>
                        </div>
                      </button>
                    ))}
                  </>
                )}
              </div>

              <button
                onClick={() => {
                  setActiveTab('detections');
                  setNotifOpen(false);
                }}
                className="w-full px-4 py-2.5 bg-slate-50 hover:bg-slate-100 text-aeris text-xs font-semibold border-t border-slate-100 transition-colors"
              >
                View All Detections
              </button>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3 pl-3 lg:pl-4 border-l border-slate-200 relative">
          <button
            onClick={() => setProfileOpen(!profileOpen)}
            className="w-8 h-8 lg:w-9 lg:h-9 rounded-full bg-slate-200 flex items-center justify-center text-slate-500 overflow-hidden"
          >
            <IconFill name="user" className="text-lg lg:text-xl" />
          </button>
          <div
            className="hidden md:block text-right cursor-pointer"
            onClick={() => setProfileOpen(!profileOpen)}
          >
            <div className="text-sm font-semibold text-slate-700 leading-tight flex items-center gap-1">
              MDRRMO Officer <Icon name="caret-down" className="text-xs text-slate-400" />
            </div>
            <div className="text-[10px] text-slate-500">Maragondon, Cavite</div>
          </div>

          {profileOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setProfileOpen(false)} />
              <div className="absolute top-full right-0 mt-3 w-48 bg-white rounded-lg shadow-lg border border-slate-200 py-1 z-50">
                <div className="px-4 py-3 border-b border-slate-100 md:hidden">
                  <div className="text-sm font-semibold text-slate-700">MDRRMO Officer</div>
                  <div className="text-[10px] text-slate-500">Maragondon, Cavite</div>
                </div>
                <button
                  onClick={() => {
                    setActiveTab('settings');
                    setProfileOpen(false);
                  }}
                  className="w-full text-left px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 hover:text-aeris transition-colors flex items-center gap-2"
                >
                  <Icon name="gear" className="text-lg" /> System Settings
                </button>
                <div className="h-px bg-slate-100 my-1" />
                <button
                  onClick={() => {
                    alert('Logging out...');
                    setProfileOpen(false);
                  }}
                  className="w-full text-left px-4 py-2 text-sm text-danger hover:bg-red-50 transition-colors flex items-center gap-2"
                >
                  <Icon name="sign-out" className="text-lg" /> Log Out
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
