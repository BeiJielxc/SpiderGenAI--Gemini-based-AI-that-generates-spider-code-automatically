import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, X } from 'lucide-react';
import { InputProps } from '../types';

const DateInput: React.FC<InputProps> = ({
  label,
  className = '',
  value,
  onChange,
  name,
  placeholder,
  ...props
}) => {
  const [showCalendar, setShowCalendar] = useState(false);
  const [currentDate, setCurrentDate] = useState(new Date());
  const [calendarPosition, setCalendarPosition] = useState({
    top: 0,
    left: 0,
    openUp: false
  });
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLDivElement>(null);
  const calendarRef = useRef<HTMLDivElement>(null);
  const CALENDAR_WIDTH = 280;
  const CALENDAR_HEIGHT = 320;

  // Parse initial value if present
  useEffect(() => {
    if (value && typeof value === 'string') {
      const date = new Date(value);
      if (!isNaN(date.getTime())) {
        setCurrentDate(date);
      }
    }
  }, []);

  // Close calendar when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      const clickedTrigger = containerRef.current?.contains(target);
      const clickedCalendar = calendarRef.current?.contains(target);
      if (!clickedTrigger && !clickedCalendar) {
        setShowCalendar(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const updateCalendarPosition = useCallback(() => {
    if (!triggerRef.current) return;

    const rect = triggerRef.current.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const spaceAbove = rect.top;
    const openUp = spaceBelow < CALENDAR_HEIGHT && spaceAbove > spaceBelow;

    let left = rect.left;
    const maxLeft = window.innerWidth - CALENDAR_WIDTH - 8;
    if (left > maxLeft) left = maxLeft;
    if (left < 8) left = 8;

    setCalendarPosition({
      top: openUp ? rect.top - 8 : rect.bottom + 8,
      left,
      openUp
    });
  }, []);

  useEffect(() => {
    if (!showCalendar) return;

    updateCalendarPosition();
    window.addEventListener('resize', updateCalendarPosition);
    window.addEventListener('scroll', updateCalendarPosition, true);

    return () => {
      window.removeEventListener('resize', updateCalendarPosition);
      window.removeEventListener('scroll', updateCalendarPosition, true);
    };
  }, [showCalendar, updateCalendarPosition]);

  const daysInMonth = (year: number, month: number) => new Date(year, month + 1, 0).getDate();
  const firstDayOfMonth = (year: number, month: number) => new Date(year, month, 1).getDay();

  const handlePrevYear = () => {
    setCurrentDate(new Date(currentDate.getFullYear() - 1, currentDate.getMonth(), 1));
  };

  const handleNextYear = () => {
    setCurrentDate(new Date(currentDate.getFullYear() + 1, currentDate.getMonth(), 1));
  };

  const handlePrevMonth = () => {
    setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() - 1, 1));
  };

  const handleNextMonth = () => {
    setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() + 1, 1));
  };

  const handleDateClick = (day: number) => {
    // Construct the date string manually to avoid timezone issues
    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();
    const formattedDate = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    
    // Toggle logic: if the selected date is already the value, clear it
    const newValue = value === formattedDate ? '' : formattedDate;
    
    // Create synthetic event for compatibility with generic handle change
    if (onChange) {
      const event = {
        target: {
          name: name,
          value: newValue
        }
      } as React.ChangeEvent<HTMLInputElement>;
      onChange(event);
    }
    setShowCalendar(false);
  };

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onChange) {
      const event = {
        target: {
          name: name,
          value: ''
        }
      } as React.ChangeEvent<HTMLInputElement>;
      onChange(event);
    }
  };

  const renderCalendar = () => {
    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();
    const daysCount = daysInMonth(year, month);
    const startDay = firstDayOfMonth(year, month);
    const days = [];

    // Empty cells for days before start of month
    for (let i = 0; i < startDay; i++) {
      days.push(<div key={`empty-${i}`} className="h-8" />);
    }

    // Days of the month
    for (let day = 1; day <= daysCount; day++) {
      const isToday = 
        day === new Date().getDate() && 
        month === new Date().getMonth() && 
        year === new Date().getFullYear();
      
      const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
      const isSelected = value === dateStr;

      days.push(
        <button
          type="button"
          key={day}
          onClick={() => handleDateClick(day)}
          className={`
            h-8 w-8 flex items-center justify-center rounded-full text-sm transition-colors
            ${isSelected 
              ? 'bg-emerald-500 text-white font-medium hover:bg-emerald-600' 
              : isToday 
                ? 'text-emerald-600 font-bold bg-emerald-50' 
                : 'text-gray-700 hover:bg-gray-100'}
          `}
        >
          {day}
        </button>
      );
    }

    if (typeof document === 'undefined') return null;

    return createPortal(
      <div
        ref={calendarRef}
        onMouseDown={(e) => e.stopPropagation()}
        className="p-4 bg-white rounded-xl shadow-xl border border-gray-100 min-w-[280px] animate-in fade-in zoom-in-95 duration-200"
        style={{
          position: 'fixed',
          top: calendarPosition.top,
          left: calendarPosition.left,
          width: `${CALENDAR_WIDTH}px`,
          zIndex: 9999,
          transform: calendarPosition.openUp ? 'translateY(-100%)' : 'none'
        }}
      >
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-0.5">
            <button
              type="button"
              onClick={handlePrevYear}
              className="p-1 hover:bg-gray-100 rounded-full text-gray-400 hover:text-gray-600"
              title="上一年"
            >
              <ChevronsLeft size={18} />
            </button>
            <button
              type="button"
              onClick={handlePrevMonth}
              className="p-1 hover:bg-gray-100 rounded-full text-gray-500"
              title="上一月"
            >
              <ChevronLeft size={20} />
            </button>
          </div>
          <span className="font-semibold text-gray-700">
            {currentDate.getFullYear()}年{currentDate.getMonth() + 1}月
          </span>
          <div className="flex items-center gap-0.5">
            <button
              type="button"
              onClick={handleNextMonth}
              className="p-1 hover:bg-gray-100 rounded-full text-gray-500"
              title="下一月"
            >
              <ChevronRight size={20} />
            </button>
            <button
              type="button"
              onClick={handleNextYear}
              className="p-1 hover:bg-gray-100 rounded-full text-gray-400 hover:text-gray-600"
              title="下一年"
            >
              <ChevronsRight size={18} />
            </button>
          </div>
        </div>
        
        <div className="grid grid-cols-7 gap-1 mb-2 text-center">
          {['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'].map(d => (
            <span key={d} className="text-xs font-medium text-gray-400">{d}</span>
          ))}
        </div>
        
        <div className="grid grid-cols-7 gap-1">
          {days}
        </div>
      </div>,
      document.body
    );
  };

  return (
    <div
      className={`flex flex-col ${label ? 'gap-1.5' : 'gap-0'} ${props.fullWidth ? 'col-span-2' : 'col-span-1'}`}
      ref={containerRef}
    >
      {label ? (
        <label className="text-sm font-medium text-gray-700 ml-1">
          {label}
        </label>
      ) : null}
      <div className="relative group" ref={triggerRef}>
        <button
          type="button"
          onClick={() => setShowCalendar(!showCalendar)}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-emerald-600 group-focus-within:text-emerald-600 transition-colors duration-200 z-10"
        >
          <CalendarIcon size={18} />
        </button>
        <input
          {...props}
          name={name}
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          autoComplete="off"
          className={`
            w-full bg-white text-gray-800 border border-gray-200 rounded-xl 
            py-2.5 pl-10 pr-10
            outline-none transition-all duration-200
            focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100
            placeholder:text-gray-300 shadow-sm hover:border-gray-300
            ${className}
          `}
        />
        {value && (
          <button
            type="button"
            onClick={handleClear}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors p-1"
          >
            <X size={16} />
          </button>
        )}
        {showCalendar && renderCalendar()}
      </div>
    </div>
  );
};

export default DateInput;
