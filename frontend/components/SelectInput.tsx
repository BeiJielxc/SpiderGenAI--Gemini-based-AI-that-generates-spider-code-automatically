import React from 'react';
import { ChevronDown } from 'lucide-react';

interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface SelectInputProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  options: SelectOption[];
  icon?: React.ReactNode;
  fullWidth?: boolean;
}

const SelectInput: React.FC<SelectInputProps> = ({
  label,
  options,
  icon,
  fullWidth = false,
  className = '',
  ...props
}) => {
  return (
    <div className={`flex flex-col gap-1.5 ${fullWidth ? 'col-span-2' : 'col-span-1'}`}>
      <label className="text-sm font-medium text-gray-700 ml-1">
        {label}
      </label>
      <div className="relative group">
        {icon && (
          <div className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 group-focus-within:text-emerald-600 transition-colors duration-200 pointer-events-none">
            {icon}
          </div>
        )}
        <select
          {...props}
          className={`
            w-full bg-white border border-gray-200 rounded-xl
            py-2.5 ${icon ? 'pl-10' : 'pl-4'} pr-10
            outline-none transition-all duration-200
            focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100
            shadow-sm hover:border-gray-300
            appearance-none cursor-pointer
            ${!props.value ? 'text-gray-400' : 'text-gray-800'}
            ${className}
          `}
        >
          <option value="" disabled className="text-gray-400">请选择模式</option>
          {options.map((opt) => (
            <option 
              key={opt.value} 
              value={opt.value} 
              disabled={opt.disabled}
              className={opt.disabled ? 'text-gray-400' : 'text-gray-800'}
            >
              {opt.label}
            </option>
          ))}
        </select>
        <div className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none">
            <ChevronDown size={18} />
        </div>
      </div>
    </div>
  );
};

export default SelectInput;