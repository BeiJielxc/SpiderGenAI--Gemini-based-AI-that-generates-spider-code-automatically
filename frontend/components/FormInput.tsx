import React from 'react';
import { X } from 'lucide-react';
import { InputProps } from '../types';

interface FormInputProps extends InputProps {
  clearable?: boolean;
  onClear?: () => void;
}

const FormInput: React.FC<FormInputProps> = ({ 
  label, 
  icon, 
  fullWidth = false, 
  clearable = false,
  onClear,
  className = '', 
  value,
  ...props 
}) => {
  const handleClear = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (onClear) {
      onClear();
    }
  };

  const showClearButton = clearable && value && String(value).length > 0;

  return (
    <div className={`flex flex-col gap-1.5 ${fullWidth ? 'col-span-2' : 'col-span-1'}`}>
      <label className="text-sm font-medium text-gray-700 ml-1">
        {label}
      </label>
      <div className="relative group">
        {icon && (
          <div className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 group-focus-within:text-emerald-600 transition-colors duration-200">
            {icon}
          </div>
        )}
        <input
          {...props}
          value={value}
          className={`
            w-full bg-white text-gray-800 border border-gray-200 rounded-xl 
            py-2.5 ${icon ? 'pl-10' : 'pl-4'} ${showClearButton ? 'pr-10' : 'pr-4'}
            outline-none transition-all duration-200
            focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100
            placeholder:text-gray-300 shadow-sm hover:border-gray-300
            ${className}
          `}
        />
        {showClearButton && (
          <button
            type="button"
            onClick={handleClear}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors p-1"
          >
            <X size={16} />
          </button>
        )}
      </div>
    </div>
  );
};

export default FormInput;
