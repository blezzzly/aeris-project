import { useState } from 'react';
import { MissionMap } from '../components/MissionMap';

type MapType = 'offline' | 'streets' | 'satellite';

const mapTypes: { id: MapType; label: string }[] = [
  { id: 'offline', label: 'Offline' },
  { id: 'streets', label: 'Streets' },
  { id: 'satellite', label: 'Satellite' },
];

export function MapPage() {
  const [mapType, setMapType] = useState<MapType>('offline');

  return (
    <div className="h-full flex flex-col">
      <div className="panel-card min-h-full flex flex-col">
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6 border-b border-slate-100 pb-4">
          <div>
            <h2 className="text-xl font-bold text-slate-800 uppercase">Map</h2>
            <p className="text-xs text-slate-500 mt-1">2D Map view with real-time UAV position.</p>
          </div>
          <div className="flex items-center gap-2 bg-slate-100 rounded-md p-0.5">
            {mapTypes.map((type) => (
              <button
                key={type.id}
                onClick={() => setMapType(type.id)}
                className={`px-3 py-1 text-xs font-medium rounded transition-colors ${
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
        <div className="flex-1 w-full h-full relative rounded-xl overflow-hidden min-h-[400px]">
          <MissionMap variant="page" mapType={mapType} onMapTypeChange={setMapType} />
        </div>
      </div>
    </div>
  );
}
