import { useCallback, useEffect, useState } from 'react';
import {
  fetchWeeklyDigests,
  updateDigestNote,
  type WeeklyDigest,
} from '@/lib/agent';

export function WeeklySidebar() {
  const [digests, setDigests] = useState<WeeklyDigest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editNote, setEditNote] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError('');
      const data = await fetchWeeklyDigests(8);
      setDigests(data);
    } catch {
      setError('Failed to load');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const startEdit = (d: WeeklyDigest) => {
    setEditingId(d.id);
    setEditNote(d.user_note);
  };

  const saveNote = async () => {
    if (editingId == null) return;
    setSaving(true);
    try {
      await updateDigestNote(editingId, editNote);
      setDigests((prev) =>
        prev.map((d) => (d.id === editingId ? { ...d, user_note: editNote } : d)),
      );
      setEditingId(null);
    } catch {
      // keep editing state on error
    } finally {
      setSaving(false);
    }
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditNote('');
  };

  if (loading) {
    return (
      <aside className="wd-sidebar">
        <h3 className="wd-title">Research Weekly</h3>
        <div className="wd-loading">Loading...</div>
      </aside>
    );
  }

  if (error) {
    return (
      <aside className="wd-sidebar">
        <h3 className="wd-title">Research Weekly</h3>
        <div className="wd-error">{error}</div>
      </aside>
    );
  }

  if (digests.length === 0) {
    return (
      <aside className="wd-sidebar">
        <h3 className="wd-title">Research Weekly</h3>
        <div className="wd-empty">No weekly digests yet. They will be auto-generated on Mondays.</div>
      </aside>
    );
  }

  return (
    <aside className="wd-sidebar">
      <h3 className="wd-title">Research Weekly</h3>
      <div className="wd-list">
        {digests.map((d) => {
          const isExpanded = expandedId === d.id;
          const isEditing = editingId === d.id;
          const label = d.week.replace('-W', ' W');

          return (
            <div
              key={d.id}
              className={`wd-card ${isExpanded ? 'wd-card--expanded' : ''}`}
              onClick={() => setExpandedId(isExpanded ? null : d.id)}
            >
              <div className="wd-card-header">
                <span className="wd-card-week">{label}</span>
                <span className="wd-card-date">{d.week_start}</span>
              </div>

              {isExpanded && (
                <div className="wd-card-body" onClick={(e) => e.stopPropagation()}>
                  <div className="wd-content">{d.content}</div>

                  <div className="wd-note-section">
                    <div className="wd-note-label">Personal notes</div>
                    {isEditing ? (
                      <div className="wd-note-edit">
                        <textarea
                          className="wd-note-textarea"
                          value={editNote}
                          onChange={(e) => setEditNote(e.target.value)}
                          rows={3}
                          placeholder="Add your reflections..."
                        />
                        <div className="wd-note-actions">
                          <button
                            type="button"
                            className="wd-note-save"
                            disabled={saving}
                            onClick={saveNote}
                          >
                            {saving ? '...' : 'Save'}
                          </button>
                          <button
                            type="button"
                            className="wd-note-cancel"
                            onClick={cancelEdit}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div
                        className="wd-note-display"
                        onClick={() => startEdit(d)}
                      >
                        {d.user_note ? (
                          <span>{d.user_note}</span>
                        ) : (
                          <span className="wd-note-placeholder">Click to add notes...</span>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
